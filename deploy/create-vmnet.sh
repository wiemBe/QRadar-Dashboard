#!/usr/bin/env bash
# Create the macvlan network that lets the QRadar-facing containers reach a
# QRadar appliance running as a libvirt VM. See docker-compose.override.yml for
# why a plain bridge does not work.
#
# Adjust PARENT/SUBNET/GATEWAY to match `ip -br addr show <your libvirt bridge>`.
# IP_RANGE must sit OUTSIDE the libvirt DHCP pool so a container can never be
# handed an address a VM already holds.
set -euo pipefail

NETWORK=qradar-vmnet          # must match networks.qradar_vm.name in the override
PARENT=virbr0
SUBNET=192.168.122.0/24
GATEWAY=192.168.122.1
IP_RANGE=192.168.122.240/28

if docker network inspect "$NETWORK" >/dev/null 2>&1; then
  echo "$NETWORK already exists"
  exit 0
fi

docker network create -d macvlan \
  -o parent="$PARENT" \
  --subnet "$SUBNET" \
  --gateway "$GATEWAY" \
  --ip-range "$IP_RANGE" \
  "$NETWORK"

echo "created $NETWORK on $PARENT"
