PYTHON ?= python3
ROOT := $(CURDIR)
export PYTHONPATH := $(ROOT)/ExposureGuard/control:$(ROOT)/exp

.PHONY: verify data-check python-tests contract-tests rq1 rq2 rq3 figures

verify: data-check python-tests contract-tests

data-check:
	sha256sum -c data/SOURCE-MANIFEST.sha256
	cd exp && $(PYTHON) freeze_manifest.py --verify
	cd data/live && sha256sum -c MANIFEST.sha256

python-tests:
	$(PYTHON) -m unittest discover -s exp -p 'test_*.py'

contract-tests:
	cd ExposureGuard/contracts && forge test --no-match-path 'test/fork/*'

rq1:
	cd exp && $(PYTHON) sim3.py
	cd exp && $(PYTHON) baselines.py
	cd exp && $(PYTHON) pertoken_baseline.py
	cd exp && $(PYTHON) detector_baseline.py
	cd exp && $(PYTHON) synth_generalization.py
	cd exp && $(PYTHON) peak_bootstrap.py

rq2:
	cd exp && $(PYTHON) oos_calibrate.py
	cd exp && $(PYTHON) delay.py
	cd exp && $(PYTHON) calibration_compare.py
	cd exp && $(PYTHON) bootstrap_policy.py
	cd exp && $(PYTHON) capacity_recovery.py
	cd exp && $(PYTHON) refresh_sweep.py
	cd exp && $(PYTHON) partition_cost.py
	cd exp && $(PYTHON) price_coverage_curve.py

rq3:
	cd exp && $(PYTHON) benchmark_control_plane.py
	cd exp && $(PYTHON) fault_matrix.py
	cd exp && $(PYTHON) layerzero_feasibility.py
	$(MAKE) contract-tests

figures:
	cd exp && $(PYTHON) fig1.py
	cd exp && $(PYTHON) fig_cn.py
	cd exp && $(PYTHON) incidents.py
