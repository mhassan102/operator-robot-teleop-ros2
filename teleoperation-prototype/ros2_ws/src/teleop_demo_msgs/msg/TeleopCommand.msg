# Stamped Cartesian teleoperation command. Twist is the tool-frame (or
# base-frame) jog rate for a 6-DOF arm, not a wheeled-base cmd_vel.
# gripper is 0.0 closed ... 1.0 open and is not part of Twist.
uint64 sequence
builtin_interfaces/Time stamp
string frame_id
string session_id
geometry_msgs/Twist twist
float64 gripper
