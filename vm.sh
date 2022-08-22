#!/bin/bash
set -eux

_CPU_OPTS=(
	host		# match cpu to host model
	-hypervisor	# remove hypervisor flag
        l3-cache=on	# provide virtual l3 cache to reduce rescheduling interrupts

	hv_reset	# provides an MSR to allow the guest to reset itself
	hv_crash	# setup crash register for guest (caveat: prevents crash dump generation)
	hv_spinlocks=0x1fff  # try to acquire spinlock 8191 times before informing hypervisor of problem
	hv_time		# sets hyper-v clocksource MSR
	hv_runtime	# keeps the virtual processor run time in 100ns units
	hv_vpindex	# provides virtual processor index MSR
	hv_synic	# required for hv_stimer; provides SynIC messages and Event
	hv_stimer	# provide synthetic timer
			#   when these registers are missing, windows will fallback to HPET then RTC
			#   this can lead to significant CPU consumption, even when virtual CPU is idle
	migratable=no	# do not allow migration; required for +invtsc
	+invtsc		# enable invariant TSC so we can disable hpet
)
_MACHINE_OPTS=(
	q35
	accel=kvm
	usb=off
	vmport=off
	dump-guest-core=off
	kernel-irqchip=on
)
CPU_OPTS=$(IFS=,; echo "${_CPU_OPTS[*]}")
MACHINE_OPTS=$(IFS=,; echo "${_MACHINE_OPTS[*]}")

QEMU_OPTS=(
    -nographic
    -no-user-config
    -nodefaults
    -name guest=Windows,debug-threads=on
    -machine "${MACHINE_OPTS}"
    -cpu "${CPU_OPTS}"
    -no-hpet
    -smp 8,sockets=1,cores=4,threads=2
    -enable-kvm
    #-monitor stdio
    -qmp unix:/run/gaming_vm.sock,server,wait=off
    -m 12G
    -mem-prealloc
    -boot order=d
    -rtc base=localtime,driftfix=slew

    -global kvm-pit.lost_tick_policy=discard
    -global ICH9-LPC.disable_s3=1
    -global ICH9-LPC.disable_s4=1

    # EFI stuff
    -drive file=/usr/share/edk2-ovmf/x64/OVMF_CODE.fd,if=pflash,format=raw,unit=0,readonly=on
    -drive if=pflash,format=raw,file=/home/sam/workspace/gaming_vm/vars.fd

    # Shared memory for sharing video framebuffer physical gpu
    #-device ivshmem-plain,memdev=ivshmem,bus=pcie.0
    #-object memory-backend-file,id=ivshmem,share=on,mem-path=/dev/shm/looking-glass,size=128M
    #-device virtio-keyboard-pci,id=input2,bus=pcie.0,addr=0xc

    # Storage
    -object iothread,id=iothread0
    -device virtio-blk-pci,drive=vol0,scsi=off,iothread=iothread0
    -drive if=none,id=vol0,file=/dev/zvol/zroot/ZVOL/gaming_vm,format=raw,cache=none,aio=native,discard=unmap,detect-zeroes=unmap,copy-on-read=on

    # Attach iso to IDE; for when Windows isnt preloaded with virtio drivers
    #-device ide-cd,drive=cd1,bus=ide.0
    #-drive if=none,id=cd1,media=cdrom,file=./Win10_21H2_English_x64.iso

    # Network
    -device virtio-net-pci,netdev=mynet0
    -netdev tap,id=mynet0,ifname=tap0,script=no,downscript=no

    # Add a seperate pcie bus for the gpu
    -device pcie-root-port,id=pcie.1,bus=pcie.0,addr=1c.0,slot=1,chassis=1
    # gpu with multifunction=on for hdmi audio
    -device vfio-pci,host=01:00.0,bus=pcie.1,addr=00.0,multifunction=on
    # hdmi audio device
    -device vfio-pci,host=01:00.1,bus=pcie.1,addr=00.1

    # thunderbolt usb hub passthrough
    -device vfio-pci,host=05:00.0,bus=pcie.0
)
qemu-system-x86_64 "${QEMU_OPTS[@]}"
