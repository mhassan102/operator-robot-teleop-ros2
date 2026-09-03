# Networking

The project uses the `ros2_teleop_poc_net` Docker bridge. No host network
interface is changed.

Default transport is **rmw_zenoh_cpp**. The robot container runs `rmw_zenohd`
on TCP 7447. Operator nodes connect as Zenoh clients to `tcp/robot:7447` via
Docker DNS on that bridge. Multicast scouting is not used.

Fall back to CycloneDDS (multicast discovery on the same bridge):

```bash
RMW_IMPLEMENTATION=rmw_cyclonedds_cpp ./scripts/start.sh
```

The operator container has the narrow `NET_ADMIN` capability so a later
milestone can attach `netem` to its container-local `eth0`. It is not privileged.

