#!/bin/bash
while true; do
  ping -c 1 8.8.8.8 > /dev/null
  sleep 60
done