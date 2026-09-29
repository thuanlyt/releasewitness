"""hidden_runner.py <hidden-suite-dir>: run held-out tests via the unittest API; print JSON results."""
import io, json, sys, unittest, warnings
warnings.simplefilter("ignore")
suite = unittest.defaultTestLoader.discover(sys.argv[1], top_level_dir=sys.argv[1])
results = {}
class R(unittest.TextTestResult):
    def addSuccess(self, t): super().addSuccess(t); results[t._testMethodName] = "ok"
    def addFailure(self, t, e): super().addFailure(t, e); results[t._testMethodName] = "FAIL"
    def addError(self, t, e): super().addError(t, e); results[getattr(t, "_testMethodName", str(t))] = "ERROR"
unittest.TextTestRunner(stream=io.StringIO(), resultclass=R).run(suite)
print(json.dumps(results))
