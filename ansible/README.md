# Ansible Automation

Idempotent playbooks for automated deployment of the RabbitMQ cluster
across SSH-accessible nodes, demonstrating configuration management
concepts applicable to both Ansible and SaltStack.

## Architecture

    Control machine (your Mac)
        |
        | SSH (key-based, no agent required)
        |
    +---+--------+----------+
    |            |          |
    node-1     node-2     node-3
    (Ubuntu 22.04 containers with SSH enabled)

The same playbooks that deploy to Docker containers deploy identically
to real VMs by changing only the inventory file.

## Prerequisites

- Ansible: pip install ansible
- sshpass: brew install sshpass
- SSH key pair for the lab nodes

## Lab node setup

The playbooks target Docker containers with SSH enabled simulating VMs.

    # Build and start SSH-enabled nodes
    docker compose -f docker-compose-ansible-lab.yml up -d --build

    # Copy SSH key to all nodes
    ssh-keygen -t ed25519 -f ~/.ssh/ansible_lab -N ""
    ssh-copy-id -i ~/.ssh/ansible_lab.pub -p 2221 root@localhost
    ssh-copy-id -i ~/.ssh/ansible_lab.pub -p 2222 root@localhost
    ssh-copy-id -i ~/.ssh/ansible_lab.pub -p 2223 root@localhost

    # Verify Ansible can reach all nodes
    ansible all -m ping

Expected output:

    node-1 | SUCCESS => { "ping": "pong" }
    node-2 | SUCCESS => { "ping": "pong" }
    node-3 | SUCCESS => { "ping": "pong" }

## Run the playbooks

    # Step 1 - base setup (packages, users, directories)
    ansible-playbook playbooks/01-base-setup.yml

    # Step 2 - deploy RabbitMQ cluster
    ansible-playbook playbooks/deploy-rabbitmq.yml

    # Step 3 - verify idempotency (run again, should show changed=0)
    ansible-playbook playbooks/deploy-rabbitmq.yml

## Idempotency verification

First run installs and configures everything:

    node-1: ok=12  changed=9   failed=0
    node-2: ok=15  changed=11  failed=0
    node-3: ok=15  changed=11  failed=0

Second run confirms desired state already exists:

    node-1: ok=11  changed=0   failed=0
    node-2: ok=14  changed=0   failed=0
    node-3: ok=14  changed=0   failed=0

Zero changes on the second run means the cluster is in exactly the desired
state. This is the behavior that makes Ansible safe to run against live
production systems without risk of disruption.

## Key concepts demonstrated

**Idempotency** - Ansible checks current state before acting. The apt module
checks if a package is already installed. The copy module checks if a file
already has the correct content. The service module checks if a service is
already running. Tasks only execute if a change is actually needed.

**Roles** - reusable self-contained units of automation. The rabbitmq role
handles installation, configuration, Erlang cookie, clustering, and HA policy
as a single composable unit. Roles separate concerns cleanly and can be
shared across playbooks.

**Jinja2 templates** - configuration files rendered per-node using variables.
The rabbitmq.conf.j2 template produces different output on each node based
on inventory_hostname and ansible facts. Same template file, different result
per host.

**Handlers** - tasks that run only when notified by a change. The restart
rabbitmq handler only fires when the config or cookie file actually changes.
Not on every run. This prevents unnecessary service restarts.

**serial: 1** - run the entire play on one node at a time. Ensures node-1
is fully configured and the cluster is bootstrapped before node-2 attempts
to join. The Ansible equivalent of Galera healthcheck dependency chaining.

**Facts** - Ansible automatically collects system information from each node
before running tasks. ansible_hostname, ansible_default_ipv4, ansible_os_family
are available as variables without any manual configuration.

**Group vars** - variables shared across all playbooks defined in one place.
Change rabbitmq_version in group_vars/all.yml and every playbook that
references it picks up the new value automatically.

## Inventory file

The inventory file defines which nodes Ansible manages. For the Docker lab:

    [rabbitmq_nodes]
    node-1 ansible_host=127.0.0.1 ansible_port=2221
    node-2 ansible_host=127.0.0.1 ansible_port=2222
    node-3 ansible_host=127.0.0.1 ansible_port=2223

For real VMs change only these values - the playbooks remain identical:

    [rabbitmq_nodes]
    node-1 ansible_host=10.0.1.11
    node-2 ansible_host=10.0.1.12
    node-3 ansible_host=10.0.1.13

## Repository structure

    ansible/
    +-- ansible.cfg              # Ansible configuration, roles path, SSH key
    +-- inventory.ini            # Target nodes and connection details
    +-- group_vars/
    |   +-- all.yml              # Shared variables (versions, passwords, cluster names)
    +-- playbooks/
    |   +-- 01-base-setup.yml    # Install base packages, create users, verify connectivity
    |   +-- deploy-rabbitmq.yml  # Full RabbitMQ cluster deployment
    +-- roles/
        +-- rabbitmq/
            +-- tasks/
            |   +-- main.yml     # Installation, configuration, clustering steps
            +-- templates/
                +-- rabbitmq.conf.j2  # Jinja2 config template rendered per node

## Ansible vs SaltStack

The MAM production system uses SaltStack. Core concepts are equivalent
but the architecture differs:

    Feature             Ansible         Salt
    Agent required      No (SSH only)   Yes (salt-minion daemon)
    Communication       Push            Pull (minions poll master)
    State language      YAML playbooks  SLS files (YAML + Jinja2)
    Real-time events    No              Yes (event bus via ZeroMQ)
    Large fleet         Slower          Faster
    Learning curve      Lower           Higher

Both use Jinja2 templating. Both enforce idempotency. Both separate
variables from logic. Ansible knowledge transfers directly to Salt
understanding - the concepts are identical, only the syntax differs.

## Troubleshooting

**ansible all -m ping fails with sshpass error:**
Install sshpass: brew install sshpass
Or use SSH key authentication (recommended):
ssh-copy-id -i ~/.ssh/ansible_lab.pub -p 2221 root@localhost

**Role not found error:**
Verify roles_path = ./roles is set in ansible.cfg and the roles directory
exists at the same level as ansible.cfg not inside the playbooks directory.

**Task shows changed on every run:**
The task is not idempotent. Common cause: using the shell or command module
instead of a purpose-built module. The command module always reports changed.
Use changed_when: false for read-only shell commands.

**SSH connection refused:**
The Docker containers may not have started yet or SSH daemon is not running.
Check with: docker compose -f docker-compose-ansible-lab.yml ps
