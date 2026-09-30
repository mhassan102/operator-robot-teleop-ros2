# P5 lab check

The robot start script takes a serial port. The SO-ARM camera launcher
takes a capture device. Both checks are parse-only. They print the
choice and exit. They do not start Docker, mlink, MediaMTX, or the
arm. Leave the gripper where it is. Do not send `g` or `h`. Do not
run either script without its parse-only variable.

`video/start.sh` stays the Orin encoder. This check uses
`video/so-arm/start.sh`.

## 1. This PC

```bash
cd /home/muhammadhassan/robots
TELEOP_ARM_PARSE_ONLY=1 ./teleoperation-prototype/scripts/start_robot_mlink.sh \
  --real-arm \
  --serial-port /dev/serial/by-id/usb-1a86_USB_Single_Serial_5B61033180-if00
```

Pass: exit 0, and stdout is:

```text
TELEOP_ARM=real
TELEOP_ARM=real; feetech gripper on /dev/serial/by-id/usb-1a86_USB_Single_Serial_5B61033180-if00
```

That by-id path is not on this PC. The script exits before it looks
for the node. No container starts. The gripper does not move.

Default port, still parse-only:

```bash
TELEOP_ARM_PARSE_ONLY=1 ./teleoperation-prototype/scripts/start_robot_mlink.sh --real-arm
```

Pass: exit 0. The second line ends with `/dev/ttyACM0`. The port is
not opened.

Camera on this PC:

```bash
SO_ARM_CAMERA_PARSE_ONLY=1 DEVICE=/dev/video2 ./video/so-arm/start.sh
```

Pass: exit 2. stderr is:

```text
ERROR: /dev/video2 is not USB2.0_CAM1 (sysfs 'USB2.0 FHD UVC WebCam: USB2.0 I'); refusing (integrated camera is /dev/video0)
```

`/dev/video2` here is this PC's webcam, not the gripper camera
`USB2.0_CAM1`. MediaMTX does not start. The script does not open the
camera. `video/so-arm/run/` stays absent.

Do not drop `SO_ARM_CAMERA_PARSE_ONLY=1`. Do not run
`video/so-arm/gst-publish.sh`. Do not run `video/start.sh`.

Copy the changed files to the robot PC:

```bash
cd /home/muhammadhassan/robots
rsync -av teleoperation-prototype/scripts/start_robot_mlink.sh \
  muhammad-osama@100.120.193.52:~/teleops_hassan/teleoperation-prototype/scripts/start_robot_mlink.sh
rsync -av teleoperation-prototype/compose.robot-mlink.real-arm.yaml \
  muhammad-osama@100.120.193.52:~/teleops_hassan/teleoperation-prototype/compose.robot-mlink.real-arm.yaml
rsync -av \
  teleoperation-prototype/ros2_ws/src/teleop_demo/teleop_demo/arm_mode.py \
  teleoperation-prototype/ros2_ws/src/teleop_demo/teleop_demo/feetech_gripper.py \
  muhammad-osama@100.120.193.52:~/teleops_hassan/teleoperation-prototype/ros2_ws/src/teleop_demo/teleop_demo/
rsync -av teleoperation-prototype/ros2_ws/src/teleop_demo/launch/robot_sim.launch.py \
  muhammad-osama@100.120.193.52:~/teleops_hassan/teleoperation-prototype/ros2_ws/src/teleop_demo/launch/robot_sim.launch.py
rsync -av --exclude run --exclude logs video/so-arm/ \
  muhammad-osama@100.120.193.52:~/teleops_hassan/video/so-arm/
```

## 2. Robot PC

Parse-only only. Do not start mlink, Docker, MediaMTX, or the arm.

```bash
cd ~/teleops_hassan
TELEOP_ARM_PARSE_ONLY=1 ./teleoperation-prototype/scripts/start_robot_mlink.sh \
  --real-arm \
  --serial-port /dev/serial/by-id/usb-1a86_USB_Single_Serial_5B61033180-if00
```

Pass: exit 0. Stdout names that by-id path on the `feetech gripper on`
line. The follower node is not opened. No `ros2-teleop-robot-mlink`
container starts. The gripper stays still.

Default port:

```bash
TELEOP_ARM_PARSE_ONLY=1 ./teleoperation-prototype/scripts/start_robot_mlink.sh --real-arm
```

Pass: exit 0. The line ends with `/dev/ttyACM0`. The port is not opened.

Gripper camera:

```bash
SO_ARM_CAMERA_PARSE_ONLY=1 DEVICE=/dev/video2 ./video/so-arm/start.sh
```

Pass: exit 0. Stdout contains `/dev/video2` and `USB2.0_CAM1`.
MediaMTX does not start. The camera is not opened.

The same check with `DEVICE` unset uses `/dev/video2` and the same
`USB2.0_CAM1` line:

```bash
SO_ARM_CAMERA_PARSE_ONLY=1 ./video/so-arm/start.sh
```

Metadata node, still parse-only:

```bash
SO_ARM_CAMERA_PARSE_ONLY=1 DEVICE=/dev/video3 ./video/so-arm/start.sh
```

Pass: exit 2. stderr says the node is metadata. MediaMTX does not start.

Do not run `video/so-arm/start.sh` or `video/so-arm/gst-publish.sh`
without `SO_ARM_CAMERA_PARSE_ONLY=1`. Do not run `video/start.sh`.
Do not send `g` or `h`.

## 3. EC2

Nothing in this check runs on EC2. Do not rsync. Do not restart
signalling. Leave coturn as it is.

To see that TCP 8765 is still the existing process:

```bash
ss -ltnp | grep 8765
```

Expect the signalling process if it was already up. Do not start a
second listener.
