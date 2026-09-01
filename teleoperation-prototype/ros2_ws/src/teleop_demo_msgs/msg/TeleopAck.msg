# Robot acknowledgement of a stamped command. source_stamp is echoed so the
# operator can compute one-way age and RTT from a shared host clock.
uint64 sequence
string session_id
builtin_interfaces/Time source_stamp
builtin_interfaces/Time receive_stamp
string disposition
