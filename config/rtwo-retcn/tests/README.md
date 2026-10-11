# RETCN release-string regression fixture

`setlocalversion` is an unmodified copy of
[`scripts/setlocalversion`](https://android.googlesource.com/kernel/common/+/9c82b71884fdada085e6a396b7a527e1f1cdf0fc/scripts/setlocalversion)
from the pinned kernel commit `9c82b71884fdada085e6a396b7a527e1f1cdf0fc`.
It retains its upstream `GPL-2.0` SPDX notice. SHA-256:

```text
3cdcdf4cb607cacf4ec5a5834d85a16d8cbae48d82f13d7fa7e20ab59469581a
```

`scripts/test-rtwo-retcn.py` verifies this hash and runs the actual upstream
script offline with a disposable `.scmversion` cache and synthetic Git state.
This tests the fixed release suffix with stock `LOCALVERSION_AUTO=y`, including
dirty/shallow-tree behavior without the cache. Production builds use their
pinned kernel checkout's script, not this fixture. It contains no firmware.
