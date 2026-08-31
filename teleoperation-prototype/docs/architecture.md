# Architecture

Milestone 1 defines two logical services built from a common ROS 2 Humble image:

- `operator`: future keyboard control, heartbeat, and monitoring;
- `robot`: future safety controller, simulator, telemetry, and camera.

Both use CycloneDDS on a dedicated Docker bridge with ROS domain ID 42 by
default. The common image reduces build time while Compose keeps runtime roles
separate.

