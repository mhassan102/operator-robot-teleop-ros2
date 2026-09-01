# Robot-side safety snapshot. connection_state is CONNECTED, TIMEOUT, or
# RESTORED. watchdog_state is OK or SAFE STOP ACTIVATED.
builtin_interfaces/Time stamp
string session_id
string frame_id
string connection_state
string watchdog_state
string last_disposition
geometry_msgs/Twist safe_twist
float64 gripper
