## 1. Bringing up the initial cluster

```
chia up cluster.yaml -y
```


## 2. Running our first loop

```
chia job submit --working-dir . -- python hello-world.py
```


## 3. Bringing up the Antigravity node in our cluster

```
chia up –-add cluster-agy.yaml -y
```


## 4. Run the "hello-world with LLM" job

```
chia job submit --working-dir . -- python hello-world-llm.py

```


## 5. Tear down the cluster

```
chia down cluster-agy.yaml -y
```

