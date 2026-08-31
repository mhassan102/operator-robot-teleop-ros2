# Networking

The project uses the `ros2_teleop_poc_net` Docker bridge. CycloneDDS multicast
discovery is enabled within that bridge. No host network interface is changed.

The operator container has the narrow `NET_ADMIN` capability so a later
milestone can attach `netem` to its container-local `eth0`. It is not privileged.

