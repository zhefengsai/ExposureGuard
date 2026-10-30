# ExposureGuard paper revision artifact

This release accompanies the revised submission of “ExposureGuard: Bounding
Aggregate Exposure in Cross-Chain Messaging Networks with Shared Verification
Roots.” It adds the event-level Ethereum valuation audit and recovery, an
inventory of unresolved messages, and the optimized static-token comparison
used in the revision.

The audit recovers 630 originally omitted Ethereum messages, estimated at
$575.9k under the accepted historical quotes. Of the remaining 2,509 original
omissions, 2,508 have no accepted historical quote for the audited asset key
and one has an unresolved receive match. The artifact leaves their USD value
unknown. It also records 121 originally priced messages that do not pass the
stricter reconstruction rules.

The revised capacity comparison uses the reconstructed historical cohort.
ExposureGuard's six-hour envelope is 12.3% smaller than the optimized static
per-token envelope at the same 100% training-arrival target. Held-out immediate
value admission is 100% for ExposureGuard and 94.71% for the static baseline.
These figures are scoped to the evaluated Ethereum trace and stated policy
class.

Reproduce the revision checks offline with:

```bash
tar -xzf data/a2_http_cache.tar.gz -C data
make revision-audit
make data-check python-tests contract-tests
```

The cache contains public explorer responses used to reconstruct historical
events. The experiment manifest records the hashes of the frozen outputs.
