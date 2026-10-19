## Bring up the cluster

```
cd hello-example
export THIS_MACHINE=$(hostname -I | awk '{print $1}')
chia up cluster.yaml
```

It will take ~10 minutes to pull the Docker containers

## Run the hello-world job

```
chia job submit --working-dir . -- python hello-world.py
```

## Add an Antigravity worker

In cluster.yaml, add:

```
    hello_antigravity:
        resources: {"antigravity_creds": 1}
        worker_setup_commands: ["source ~/.bashrc"]
        num_workers: 1
        compatible_ips: ["${THIS_MACHINE}"]
        docker:
            image: ghcr.io/ucb-bar/chia-antigravity:latest
            container_name: "chia-antigravity-${USER}"
            run_options:
                - --ulimit nofile=65536:65536
                - --shm-size=10.24gb
                - "--user $(id -u):$(id -g)"
                - "-v ${HOME}/.gemini:/home/ray/.gemini"
            run_setup_commands:
                - echo "user:x:$(id -u):$(id -g)::/home/ray:/bin/bash" >> /etc/passwd
```

## Bring up the Antigravity node in our cluster

```
chia up –add cluster.yaml
```

## Run the "hello-world with LLM" job

```
chia job submit --working-dir . -- python hello-world.py
```
