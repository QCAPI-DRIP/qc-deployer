#!/bin/bash

apt update -y
apt install openssh-server sudo -y
service ssh start

getent passwd vm_user > /dev/null 2&>1

if [ $? -eq 0 ]; then
    echo "user exists"
else
    useradd vm_user -s /bin/bash -m
    echo "vm_user ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers
fi

FILE=/home/vm_user/.ssh/authorized_keys

if [ -f "$FILE" ]; then
    echo "$FILE exists."
else
    mkdir -p /home/vm_user/.ssh && touch /home/vm_user/.ssh/authorized_keys
    cat /tmp/id_rsa.pub >> /home/vm_user/.ssh/authorized_keys
fi

cat /home/vm_user/.ssh/authorized_keys

tail -f /dev/null
