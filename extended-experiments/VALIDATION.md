# Validation performed for this artifact update

Validated with CPython 3.12 and the versions in `requirements.txt`.

* Standard-library verifier: all packaged checksums, 459 SF10 observations,
  20 temporal groups, storage conversions, and frozen query/operation files pass.
* Both CSV-only plotting commands run from outside the repository via
  `reproduce.sh`; all eight appendix panel PDFs are produced.
* Both report-to-CSV extraction commands run with the packaged reports. Numeric
  values agree with the paper-working-tree CSVs. T-LDBC provenance hashes were
  refreshed to match the currently archived report; measured values were unchanged.
* Forward rewriting all 320,000 frozen Pokec operations produces the packaged
  PETG stream byte-for-byte (SHA-256 starts `46a3405d07a4790b`).
* All 22 regenerated AeonG Pokec query-file hashes match the campaign's archived
  manifest; these byte-identical requests are included.
* Offline unit tests cover CSV header/label conversion without modifying source,
  refusal to overwrite converted output, operator-runner command parameters,
  and temporal operation rewriting. Run with:

  ```bash
  python3 -m unittest discover -s scripts -p 'test_*.py'
  ```

No fresh SF10 or temporal database benchmark was executed during packaging.
Remote experiment servers were not accessed. Large data acquisition, original
Docker binary recovery, and end-to-end database timing remain environment-specific
rerun steps, as documented in the two guides. No claim of bit-identical engine
recovery or exact timing reproduction is made.
