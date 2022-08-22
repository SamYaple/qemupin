#!/usr/bin/python

import json
import os
import socket 
import subprocess
import sys 
import time

###############################################################################
#        Configuration section (because im too lazy for opts)                 #
###############################################################################
QEMU_QMP_SOCK = "/run/gaming_vm.sock"

# This info is cpu specific and should be changed based on output from:
#   * lscpu -e
#   * egrep '(processor|core id)' /proc/cpuinfo

# Provide a visual mapping of the cpu topology mapping processer ids with thier
# hyperthreaded cores. In this case, cpu 0 is a hyperthreaded peer with cpu 8
ALL_CPUS     = [ 0,  1,  2,  3,  4,  5,  6,  7,
                 8,  9, 10, 11, 12, 13, 14, 15]

# Pinning all host processes to the first three cores
HOST_CPUS    = [ 0,  1,  2,
                 8,  9, 10]

# Pinning qemu management threads to host process cpuset
MGMT_CPUS    = HOST_CPUS

# Dedicating a core to qemu io threads
QEMU_IO_CPUS = [ 3,
                11]

# Isolating guest cpus from host processes
QEMU_CPUS    = [ 4,  5,  6,  7,
                12, 13, 14, 15]

###############################################################################
###############################################################################


class QMP:
    # These are raw strings that get sent directly to QEMU. The response is parsed
    # into a python dict/lists. Afterward you're on your own to interpret the data
    HANDSHAKE        = b'{"execute": "qmp_capabilities"}\n'
    QUERY_CPUS       = b'{"execute": "query-cpus-fast"}\n'
    QUERY_IO_THREADS = b'{"execute": "query-iothreads"}\n'

    def __init__(self, socket_path=QEMU_QMP_SOCK):
        self.socket_path = str(socket_path)
        
    def __enter__(self):
        self.stream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.stream.connect(self.socket_path)
        version_info = self._read_json_from_socket()
        handshake_resp = self.execute(QMP.HANDSHAKE)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.stream.close()

    def execute(self, message):
        self.stream.send(message)
        return self._read_json_from_socket()
    
    def _read_json_from_socket(self):
        data = b""
        while True:
            partial_data = self.stream.recv(4096)
            data += partial_data
            if (len(partial_data) != 4096):
                # We read all the available data on the buffer; now exit loop
                break
        return json.loads(data.strip())
    
    @staticmethod
    def _add_list_if_needed(data, desired_index):
        # TODO: pretend this function never existed in a refactor
        if len(data) == desired_index:
            data.append([])
        return data
    
    def query_cpu_topology(self):
        cpu_info = self.execute(QMP.QUERY_CPUS)
        if 'return' not in cpu_info:
            raise(f"unknown response to QUERY_CPUS: {cpu_info}")
    
        cpus = []
        for cpu in cpu_info['return']:
            socket_id = cpu['props']['socket-id']
            self._add_list_if_needed(cpus, socket_id)
                
            core_id   = cpu['props']['core-id']
            self._add_list_if_needed(cpus[socket_id], core_id)
    
            thread_id = cpu['props']['thread-id']
            self._add_list_if_needed(cpus[socket_id][core_id], thread_id)
    
            cpus[socket_id][core_id][thread_id] = {
                'vm_architecture': cpu['target'],
                'vm_cpu_index':    int(cpu['cpu-index']),
                'os_thread_id':    int(cpu['thread-id']),
            }
        return cpus

    def query_io_threads(self):
        io_threads = self.execute(QMP.QUERY_IO_THREADS)
        if 'return' not in io_threads:
            raise(f"unknown response to QUERY_IO_THREADS: {io_threads}")
        return io_threads['return']


def update_systemd_slices_cpuset(cpuset):
    cpu_list = ','.join(str(s) for s in cpuset)
    for sslice in ["user", "system", "init"]:
        cmd = [
            "systemctl",
            "set-property",
            "--runtime",
            "--",
            f"{sslice}.slice",
            f"AllowedCPUs={cpu_list}",
        ]
        subprocess.run(cmd, check=True)


def main():
    update_systemd_slices_cpuset(HOST_CPUS)

    mgmt_pids = []
    with QMP() as qmp:
        vcpu_info  = qmp.query_cpu_topology()
        io_threads = qmp.query_io_threads()

        # Get the parent pid from the socket while we are still attached
        ppid = qmp.stream.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED)
        mgmt_pids.append(ppid)

    if io_threads:
        for pid in [pid['thread-id'] for pid in io_threads]:
            print(f"iothread -> {pid:8} -> {QEMU_IO_CPUS}")
            os.sched_setaffinity(pid, QEMU_IO_CPUS)

    # TODO: still missing mon_iothread and im assuming others
    for pid in mgmt_pids:
        print(f"worker   -> {pid:8} -> {MGMT_CPUS}")
        os.sched_setaffinity(pid, MGMT_CPUS)

    for sidx, cpu_socket in enumerate(vcpu_info):
        for cidx, cpu_core in enumerate(cpu_socket):
            idx = cidx
            for tidx, cpu_thread in enumerate(cpu_core):
                # anytime we see a second thread for a core, map the second
                # thread to the physical host cpu peer core
                idx += tidx * int(len(QEMU_CPUS) / 2)
                thread_pid = cpu_thread['os_thread_id']
                processor = QEMU_CPUS[idx] 
                print(f"{sidx:2} {cidx:2} {tidx:2} -> {thread_pid:8} -> {processor:2}")

                # Use python to pin affinity
                os.sched_setaffinity(thread_pid, {processor})

    # return all cpus to host
    #update_systemd_slices_cpuset(ALL_CPUS)

if __name__ == '__main__':
    main()
