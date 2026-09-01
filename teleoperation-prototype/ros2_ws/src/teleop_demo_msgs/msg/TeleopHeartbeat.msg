# Operator liveness. Independent of motion so the watchdog can distinguish
# "operator idle" from "operator gone".
uint64 sequence
builtin_interfaces/Time stamp
string session_id
