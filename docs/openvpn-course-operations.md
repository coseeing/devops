# Course OpenVPN Operations

This guide is for the operator of the shared OpenVPN service and for course
participants using a Mac. Keep the values used for one deployment together:
the VPN endpoint, the Linux host, the Windows subnet, and the VMS Portal
account. Do not put a profile, private key, or presigned URL in source control,
chat history, or CI logs.

## Scope and Security Boundary

The workflow and Ansible role install OpenVPN on the existing Linux EC2 host,
route only the managed Windows subnet through the tunnel, and allow forwarded
traffic only to TCP port 3389. They do not change VMS Portal, Windows hosts,
route tables, Network ACLs, Docker daemon configuration, Traefik, or Security
Groups. In particular, this deployment does not modify Security Groups.

On a Docker host, the firewall places a uniquely commented, course-owned jump
in Docker's administrator-owned `DOCKER-USER` chain through the `iptables-nft`
backend. That is required because a separate nftables forward base chain cannot
override Docker's or the host's default `FORWARD` drop policy. The role does not restart Docker, edit its daemon/configuration, stop containers, or touch
Docker-owned rules beyond that exact jump and its `OPENVPN-COURSE-*` chains. If
`iptables-nft` or `DOCKER-USER` is unavailable, the firewall preflight fails
closed before the role enables IPv4 forwarding or starts OpenVPN.

A shared client certificate is used for all course Macs. Multiple Macs may be
connected at once, but the certificate does not provide per-person identity or
audit evidence. A rotation revokes the shared certificate for every participant
and requires a new profile for every device.

## AWS Prerequisites

Complete and verify these network controls separately before a participant
tries to connect:

- Linux EC2 inbound: UDP 1194 from the intended client networks.
- Windows inbound: TCP 3389 from the Linux host private IPv4 address as a
  `/32` source rule.
- When VPN-only RDP is required, remove public RDP (TCP 3389) inbound rules
  from the Windows Security Group separately. The workflow never performs this
  removal and cannot prevent direct RDP to a Windows public address while such a
  rule remains.

The Linux host and Windows instances must be in the same VPC, and the Linux
host must be able to reach the Windows private subnet. Do not broaden the
Windows rule to the whole VPC when the Linux private IPv4 `/32` is available.

## Validate and Deploy

Run `.github/workflows/deploy-openvpn.yml` with **Actions > Validate or deploy
course OpenVPN > Run workflow**. Supply these inputs exactly:

| Input | Value |
| --- | --- |
| `action` | `validate` for checks only, or `deploy` to change the Linux host and the dedicated profile bucket |
| `stack_name` | Existing Linux EC2 CloudFormation stack name (default `coseeing-stack-v2`) |
| `vpn_endpoint` | Stable hostname or IPv4 address, without `https://` and without a port |
| `windows_subnet_id` | The subnet ID containing the managed Windows VMs |
| `vpn_cidr` | Non-overlapping private IPv4 client network (default `10.250.0.0/24`) |
| `client_days` | Shared certificate lifetime from 1 through 365 days (default `30`) |
| `confirm_endpoint` | Required only for deploy; type the exact same string as `vpn_endpoint`, including case and punctuation |

Select `validate` first. It runs tests and input checks and does not deploy or
query AWS credentials in the validation job. A deploy is guarded by both
`action == deploy` and the exact endpoint confirmation; a missing or different
confirmation skips the deploy job. The deploy job discovers the existing Linux
host and Windows/VPC CIDRs read-only, creates or updates only the dedicated
OpenVPN distribution stack, and then invokes the OpenVPN Ansible playbook. It
does not modify Security Groups.

Do not trigger a deploy until the endpoint, Linux stack, Windows subnet, VPN
CIDR, and certificate lifetime have been reviewed by the operator. Local checks
and a successful workflow validation do not prove AWS ingress, Mac behavior,
or Windows RDP.

## Inspect Service Status

SSH to the Linux host and run:

```bash
sudo openvpn-course status
```

The output includes the `openvpn-server@course` service state, UDP 1194
listener count, endpoint, VPN and Windows CIDRs, connected-client count, and
the shared certificate expiry. It does not print profile or private-key
contents. A healthy deployment should show an active service, a UDP 1194
listener, and the expected CIDRs.

## Export a Profile over SSH

The generated profile and PKI remain root-readable only on the Linux host. To
make a one-device export for the invoking SSH user, run the following on the
Linux host (replace the destination with a private local path):

```bash
sudo openvpn-course export ./course-vpn.ovpn
```

The destination is created with mode `0600` and, when invoked through `sudo`,
is owned by the original sudo user. Existing files are not overwritten unless
`--force` is explicit:

```bash
sudo openvpn-course export ./course-vpn.ovpn --force
```

For a controlled SSH transfer, export to a temporary file in the operator's
home directory, copy it over an already authenticated SSH connection, then
remove the temporary file from both endpoints. Never paste the file into a
terminal or log. For classroom distribution, prefer `share` below so no SSH
private key or profile contents are printed by the command.

## Share a Profile for 10 Minutes

On the Linux host, run:

```bash
sudo openvpn-course share
```

The command uploads the current profile to a random private S3 object key and
prints a bearer URL plus an expiry timestamp. The requested URL lifetime is
600 seconds (10 minutes), and the bucket policy enforces the same maximum
signature age. The effective lifetime can be shorter if the EC2 role's
temporary signing credentials expire first. Send the URL only through a
trusted channel and treat anyone who receives it as able to download the
shared profile during its valid period.

The displayed `Expires no later than` bound is calculated immediately before `aws s3 presign`; the command prints the generated URL exactly once. The
`share` operation holds the root-owned mutation lock for its complete update.
If another rotation or share currently owns that lock, it fails without S3
changes with `another rotate or share operation is already in progress`.

The command first best-effort deletes the previously recorded transient object
before uploading or presigning the new one. If that cleanup succeeds and a
later upload or presign fails, the previous URL/object may already be unavailable;
rerun `sudo openvpn-course share` to create a new share. If
cleanup or a later operation fails, S3 lifecycle expiration remains the
fallback for abandoned objects. Lifecycle expiration makes objects eligible
for deletion after one day; physical deletion is asynchronous. URL expiration
blocks new or restarted downloads, but does not invalidate an `.ovpn` file
already downloaded to a Mac. Use `rotate` to revoke the certificate in a
downloaded profile.

## Rotate Every Distributed Profile

Rotation is a course-wide operation. It creates and validates a replacement
certificate/profile, then revokes the currently active shared certificate and
reloads OpenVPN. Existing sessions may be disconnected, and every previously
distributed profile becomes unusable for a new connection. Distribute the new
profile to every participant after rotation.

Run:

```bash
sudo openvpn-course rotate --days 30
```

At the prompt, type this exact confirmation:

```text
ROTATE course-shared
```

If generation, validation, or activation fails, the command restores the
previous PKI/profile state where possible. Do not delete PKI files manually.
`rotate` holds the same root-owned mutation lock as `share`; if it is busy, the
command reports `another rotate or share operation is already in progress` and
makes no PKI changes. The lock serializes only those mutations and does not lock `status`, `export`, or `logs`.

## Read Service Logs

Show the last 100 service records:

```bash
sudo openvpn-course logs
```

Use these records for connection, disconnection, and service-error diagnosis.
Because all participants share one certificate, logs are not per-person audit
evidence. If the service is not active, also use the status command and inspect
the host's systemd/journal output before changing configuration.

## Import on macOS

1. On the Mac, download the profile from the trusted share URL before its
   10-minute expiry. Save it as `course-vpn.ovpn` in a private folder and keep
   permissions restricted. Do not share the file after downloading it.
2. Install OpenVPN Connect or Tunnelblick from the organization's approved
   source.
3. In OpenVPN Connect, choose **Import Profile**, select the `.ovpn` file, and
   connect. In Tunnelblick, open the `.ovpn` file and approve installation for
   the current user.
4. Confirm the client shows an established VPN connection before testing RDP.

The profile intentionally has no `redirect-gateway`, DNS push, IPv6 route, or
compression setting. Only the configured Windows subnet enters the tunnel.

## Find the Windows Private IPv4 in VMS Portal

For a normal user, enter the exact EC2 Instance ID in the VMS Portal
lookup form. The lookup result provides the VM's **Private IPv4**; use that
exact address for RDP. An admin may browse/list records and copy the **Private
IPv4** from the managed Windows VM record. Neither path should use the public
address when testing VPN-only access. Confirm that the VM is running and that
the Windows Security Group allows TCP 3389 from the Linux host private IPv4
`/32`.

## Connect with Microsoft Remote Desktop

Open Microsoft Remote Desktop on the Mac, create or edit a PC entry, and set
the PC name to the Windows **Private IPv4** from VMS Portal. Use the Windows
credentials supplied by the course administrator and connect only after the
OpenVPN client is connected. Do not save the `.ovpn` profile or Windows
password in an unprotected shared location.

## Verify Split Tunnel and Blocked Traffic

Perform these checks from the Mac after connecting to OpenVPN. Record the
results for the specific VPN session without including the profile or URL.

1. Before connecting, record the Mac's ordinary public IPv4:

   ```bash
   curl -4 https://checkip.amazonaws.com
   ```

   Run the same command while the VPN is connected. The public IPv4 should be
   unchanged: ordinary internet and DNS traffic remain on the Mac's existing
   connection.

2. The expected RDP path is allowed:

   ```bash
   nc -vz -w 5 WINDOWS_PRIVATE_IPV4 3389
   ```

   Replace `WINDOWS_PRIVATE_IPV4` with the VMS Portal value. Expect a
   successful TCP connection when the Windows RDP service and Security Group
   rule are ready. Microsoft Remote Desktop should then connect to the same
   private address.

3. A non-RDP Windows port is blocked by the Linux VPN firewall:

   ```bash
   nc -vz -w 5 WINDOWS_PRIVATE_IPV4 445
   ```

   Expect failure or timeout. Repeat with any other non-3389 port required by
   the course test plan; only TCP 3389 is forwarded.

4. Do not treat a route lookup alone as proof of authorization. The tunnel may
   carry packets addressed to the Windows subnet, but the Linux firewall drops
   every forwarded destination/port other than TCP 3389. Traffic to unrelated
   VPC destinations, Linux tunnel services, and other VPN clients must also be
   denied.

## Confirm Presigned URL Expiration

Run `share` on the Linux host. Copy only the URL (not the expiry label) into a
hidden shell prompt so it is not echoed or placed in shell history:

```bash
sudo openvpn-course share
```

Then, in a trusted Mac terminal, type the URL when prompted and download once
immediately. Wait at least 601 seconds and retry the same URL:

```bash
read -r -s PRESIGNED_URL
curl --fail --location --output /tmp/course-vpn-expiry-test.ovpn "$PRESIGNED_URL"
sleep 601
curl --fail --location --output /tmp/course-vpn-expired.ovpn "$PRESIGNED_URL"
unset PRESIGNED_URL
```

The first download should succeed and the second should fail with an expired
signature/authorization response. Remove both test files afterward. This test
checks that the URL cannot start a new download after 10 minutes; it does not
test certificate revocation. A profile already downloaded before expiry can
still be imported and used until its certificate expires or rotation revokes
it.

## Recover or Remove Only OpenVPN Components

For a service-only recovery, preserve the PKI and profile and restart only the
named OpenVPN components:

```bash
sudo systemctl restart openvpn-course-firewall openvpn-server@course
sudo openvpn-course status
```

If a staged firewall update must be removed, include that action only in the
ordered teardown below. The scoped helper removes only its uniquely commented
`DOCKER-USER jump`, `OPENVPN-COURSE-*` chains, the legacy
`inet openvpn_course` table when present, and `ip openvpn_course_nat`; never
remove those rules/tables while the VPN service is running.

Before removing the deployment, save any incident evidence and confirm that no
participant needs the service. Stop and disable only the two OpenVPN units:

```bash
sudo systemctl stop openvpn-server@course
sudo systemctl disable openvpn-server@course
sudo systemctl stop openvpn-course-firewall
sudo systemctl disable openvpn-course-firewall
sudo openvpn-course-firewall remove
```

The OpenVPN service must be stopped and disabled before stopping, disabling, or
removing the firewall; never remove the firewall while the VPN service runs.

Do not remove `/etc/openvpn/course-pki`, `/var/lib/openvpn-course`, the server
configuration, or the S3 distribution stack as an ad-hoc recovery step. Those
contain the shared credential and state needed to restore the service; removal
or bucket cleanup requires a separately approved teardown procedure. Do not
touch VMS Portal, Windows configuration, Security Groups, Network ACLs,
Docker, Traefik, or unrelated systemd units. If the host is rebuilt, the design
does not provide an external CA backup: generate a new PKI, deploy it through
the workflow, and distribute a new profile.

## Validation Boundary

The repository's automated checks validate workflow structure, Ansible syntax,
CloudFormation structure, shell syntax, and operator-command behavior. No live
AWS deployment, AWS resource query, Mac OpenVPN import, public-IP comparison,
or Windows RDP test has been performed as part of this documentation change.
Those results must be recorded separately after an explicitly authorized
deployment and the manual smoke test above.
