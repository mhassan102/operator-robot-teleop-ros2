# Stamped operator command. The Twist payload is retained so a later safety
# layer can extract a standard geometry_msgs/Twist for the simulator edge.
uint64 sequence
builtin_interfaces/Time stamp
geometry_msgs/Twist twist
string session_id
