# Accounting scope and reconfiguration check

This release retains the v1.3.0 experiment data and delivery implementation.
It clarifies that the accounting envelope applies while bucket capacities
and floor weights remain fixed. A new contract regression records that
normal floor reallocation can initialize fresh credit without raising the
global budget. Intervals crossing capacity updates require separate
accounting for each configuration.

The frozen replay results remain unchanged. The offline suite now includes
44 Python tests and 57 Solidity tests. See the README for reproduction.
