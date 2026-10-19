# Debug section — code for the slides

Everything participants type. Paths assume `~/chia-tutorial`.

---

## 1. Caching and bypassing

### The yaml — which calls

`debugging/caching/cache.yaml` (record) and `replay.yaml` (replay):

```yaml
cache:
  ChiselBuildNode.build: {cache: true, tags: ["hw_.*"]}
bypass:
  ChiselBuildNode.build: {bypass: true, tags: ["hw_.*"]}
```

Keys are the qualified function name. `tags:` are regexes over `_chia_tag`.

### The call site — name the call

```python
builder = ChiselBuildNode(...)
ref = builder.build.chia_remote(builder, _chia_tag=f"hw_iter_{i}")
artifact = get(ref)
```

No tag, no cache: the write path keys on it.

### The driver — load it and say how to replay

```python
start_cache(size=64, units="GB", cache_dir_path=CACHE_DIR, yaml_path=CFG)
Bypass(yaml_path=CFG)
get_active_bypass().set_provider("ChiselBuildNode.build", cache_provider)
get_active_bypass().set_cond("ChiselBuildNode.build", cache_hit_cond)
```

```python
def cache_provider(tag, data_path, *args, **kwargs):
    hit, value = get(get_active_cache().read.chia_remote(tag))
    return value

def cache_hit_cond(tag, data_path, *args, **kwargs):
    return get(get_active_cache().has.chia_remote(tag))
```

`set_provider` says *what* a replayed call returns; `set_cond` says *whether*
it is safe to replay — without it a cold cache raises instead of running.

### Run it

```bash
cd ~/chia-tutorial/debugging/caching
chia job submit --chia-cluster ../../accel/cluster.yaml -- \
    python "$(pwd)/accel_loop.py" --iterations 3            # record
chia job submit --chia-cluster ../../accel/cluster.yaml -- \
    python "$(pwd)/accel_loop.py" --iterations 3 --replay   # replay
```

---

## 2. Profiling

### In the code

```python
start_collector(log_dir="debugging/profiling/out/profile_1")   # driver, right after ray.init()
get_profiler().add_info({"cycles": cycles})                    # inside a node
get_profiler().log_event("phase", name="baseline")             # anywhere
stop_collector()
```

### Rendering

```bash
chia viz-profile <log> --format html     # interactive timeline
chia viz-profile <log> --format svg      # dependency graph
chia viz-profile <log> --format table    # CSV, exec time per function
```

```bash
cd ~/chia-tutorial/debugging/profiling
python debug_render_profile.py           # newest profile, all three at once
```
