# Course OpenVPN Operations

[中文版：部署參數、S3、憑證、Mac 安裝與系統架構圖](openvpn-course-operations.zh-TW.md)

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
Docker-owned rules beyond that exact jump and its `OPENVPN-COURSE-*` chains.
The `FORWARD` chain must itself jump to `DOCKER-USER`; merely having a
`DOCKER-USER` chain is not sufficient. If `iptables-nft`, `DOCKER-USER`, or
that `FORWARD`-to-`DOCKER-USER` path is unavailable, the firewall preflight
fails closed before the role enables IPv4 forwarding or starts OpenVPN.

The course also owns `inet openvpn_course_input`, whose input base chain has a
terminal drop for all `iifname "tun-course"` traffic except the NVDA Remote
relay (TCP 6837). It prevents VPN clients from reaching any other Linux host
service through the tunnel while safely coexisting with other host input
policy: its drop verdict is terminal. The same chain accepts TCP 6837 only from
the VPN client network, the Windows subnet, and loopback, and drops it from
every other source, including the public interface. The helper
atomically replaces only that table and `ip openvpn_course_nat` (and removes
the legacy `inet openvpn_course` table if it is still present).

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
- Linux EC2 inbound: TCP 6837 (NVDA Remote) from the Windows Security Group or
  the Windows subnet CIDR. Never open TCP 6837 to `0.0.0.0/0`. VPN clients reach
  the relay inside the tunnel and need no extra Security Group rule.
- When VPN-only RDP is required, remove public RDP (TCP 3389) inbound rules
  from the Windows Security Group separately. The workflow never performs this
  removal and cannot prevent direct RDP to a Windows public address while such a
  rule remains.

The Linux host and Windows instances must be in the same VPC, and the Linux
host must be able to reach the Windows private subnet. Do not broaden the
Windows rule to the whole VPC when the Linux private IPv4 `/32` is available.

The protected `a11y-village-production` environment must provide
`AWS_GITHUB_ACTION_ROLE`, `EC2_SSH_KEY`, and `OPENVPN_SSH_KNOWN_HOSTS` secrets.
The last value is a complete `known_hosts` entry for the public IP returned by
the Linux stack, for example `<PUBLIC_IP> ssh-ed25519 <BASE64_KEY>`. Obtain the
host public key through a trusted console or SSM session and verify its
fingerprint out of band before saving it. Do not trust an unverified
`ssh-keyscan` result. Update the secret when the instance, public IP, or SSH
host key changes; otherwise deployment and profile management intentionally
fail closed.

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
| `client_days` | Initial shared certificate lifetime from 1 through 365 days (default `30`); later deploys preserve the existing certificate |
| `confirm_endpoint` | Required only for deploy; type the exact same string as `vpn_endpoint`, including case and punctuation |

Select `validate` first. Its credential-free job runs repository and static
checks only; it does not validate the supplied deployment inputs against AWS
networks and does not query AWS credentials. A deploy is guarded by both
`action == deploy` and the exact endpoint confirmation; a missing or different
confirmation skips the deploy job. Only after that guard, the deploy job uses
read-only AWS discovery for the Linux host and Windows/VPC CIDRs, validates the
deployment inputs against those discovered values, creates or updates only the
dedicated OpenVPN distribution stack, and then invokes the OpenVPN Ansible
playbook. A deploy creates the initial shared certificate only when none exists;
later deploys preserve the current certificate and do not share the profile or
update its Secrets Manager value. It does not modify Security Groups.

## Rotate or Share from GitHub Actions

Run `.github/workflows/manage-openvpn-profile.yml` with **Actions > Manage
course OpenVPN profile > Run workflow**. This workflow is manually dispatched
and offers two operations:

- `share` leaves the certificate unchanged, creates a new 10-minute URL for
  the current profile, and updates `prod/vms-portal/openvpn-profile`.
- `rotate` requires `confirm_rotation` to equal `ROTATE course-shared`. It
  creates and validates a new certificate for `client_days`, revokes the old
  certificate, activates the new profile, then shares it and updates the same
  secret.

Both operations store JSON fields `url` and `expires_at`. The URL is masked and
is not written to a GitHub output or job summary. Deployment and profile
management use the same concurrency group, so deploy, rotate, and share cannot
race with one another. Its `queue: max` setting retains up to 100 pending runs
instead of replacing the previously pending run. The GitHub OIDC role needs
`secretsmanager:DescribeSecret`, `secretsmanager:CreateSecret`, and
`secretsmanager:PutSecretValue` scoped to
`arn:aws:secretsmanager:ap-northeast-1:<ACCOUNT_ID>:secret:prod/vms-portal/openvpn-profile-??????`.
The six `?` characters intentionally match the random six-character suffix
that Secrets Manager adds to the ARN. An operator retrieving the URL separately
needs `secretsmanager:GetSecretValue` on the same ARN.

If rotation fails before activation, the host command restores the previous
profile state where possible. If rotation succeeds but sharing or the Secrets
Manager write fails, the new certificate remains active; rerun this workflow
with `action=share` to publish it without another rotation.

If the SSH connection is lost and GitHub cannot tell whether rotation
completed, do not immediately rerun `rotate`. Log in through a trusted path,
run `sudo openvpn-course status`, and inspect
`sudo cat /var/lib/openvpn-course/active-client-cn`. A newly rotated CN contains
the rotation timestamp. If the new CN and expected expiry are active, rerun
only `action=share`; rerun `rotate` only after confirming that the old CN is
still active.

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

The **Manage course OpenVPN profile** workflow invokes this command for both
`share` and successful `rotate` operations and stores
`{"url":"...","expires_at":"..."}` in the dedicated
`prod/vms-portal/openvpn-profile` secret. Secrets Manager does not extend the
URL lifetime, so an authorized operator must retrieve and deliver it before
`expires_at`. Running `share` manually prints a new URL but does not update the
Secrets Manager value.

Immediately after `aws s3 presign` returns, the displayed `Expires no later than` bound is
calculated immediately as that returned-clock time plus 600 seconds; the
signature timestamp can only be at or before the command returned, so this is
a true upper bound. The command prints the generated URL exactly once. The
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

Prefer the **Manage course OpenVPN profile** workflow with `action=rotate`, the
desired `client_days`, and exact confirmation `ROTATE course-shared`. It runs
the host operation below and publishes the resulting profile URL to Secrets
Manager. An X.509 certificate's expiry cannot be extended in place; changing
the lifetime always means issuing and activating a replacement certificate.

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

## Connect with NVDA Remote

The same playbook runs the NVDA Remote relay server
([jmdaweb/NVDARemoteServer](https://github.com/jmdaweb/NVDARemoteServer), a
digest-pinned Docker image) on the Linux host as the systemd unit
`nvda-remote-server`, on TCP 6837 with host networking. It logs connection
events without channel keys.

| Client | NVDA Remote host | Port |
| --- | --- | --- |
| VPN user's own Windows PC (OpenVPN connected) | First address of `vpn_cidr`, e.g. `10.250.0.1` | 6837 |
| Windows VM | Linux host **Private IPv4**, e.g. `10.0.1.10` | 6837 |

Both sides choose **Connect to a control server**, enter the address for their
side and port 6837, and type the same key; one side controls, the other is
controlled.

### Certificate trust

NVDA verifies the relay certificate against the Windows trust store first and
only shows an "untrusted certificate" prompt when that fails. The deployment
therefore keeps a private CA on the Linux host in
`/etc/nvda-remote-server/ca` (created once, private key never leaves the host)
and issues a server certificate whose IP SANs are exactly the two addresses
above. The CA is name-constrained to private IPv4 ranges and cannot sign usable
DNS names or public IPs, so trusting it cannot be abused to intercept other
sites. A daily `nvda-remote-cert-renew.timer` reissues the server certificate
30 days before expiry, or when the addresses change; clients keep trusting the
same CA.

- **Windows VMs:** the workflow deploys the `nvda-remote-ca-trust`
  CloudFormation stack, an SSM State Manager association that imports the CA
  into `LocalMachine\Root` on every instance tagged `VmPortalManaged=true`.
  It also applies to VMs that are stopped now or launched later once their SSM
  agent checks in, and re-runs daily. No prompt appears on the VMs.
- **VPN users:** sign in to VMS Portal, open the **NVDA Remote** section,
  download `nvda-remote-ca-install.cmd`, and run it once on the PC they use with
  OpenVPN. No administrator rights are needed; Windows asks once whether to
  install the certificate. The section also lists both relay addresses and the
  CA SHA-256 fingerprint. Downloads require a portal login and are audited.
  Users who skip this see NVDA's certificate prompt instead, and again after
  each yearly server certificate renewal.

  Manual fallback: copy the PEM block from the workflow run summary into
  `nvda-remote-ca.crt` and run
  `Import-Certificate -FilePath .\nvda-remote-ca.crt -CertStoreLocation Cert:\CurrentUser\Root`.

The workflow stores the public CA certificate and both relay addresses in the
Secrets Manager secret `prod/vms-portal/nvda-remote` (part of the
`nvda-remote-ca-trust` stack). VMS Portal reads it with `NVDA_REMOTE_SECRET_ID`
and hides the section until the secret exists, so redeploy VMS Portal once
after this change to pick up the setting and its read permission.

#### When the CA changes

The CA is valid for 10 years and changes only in these cases:

- **First deployment** creates it.
- **The deploy workflow finds it within a year of expiry** and rotates it.
  Only the workflow rotates the CA, because it also publishes the new CA to the
  Windows VMs and VMS Portal in the same run. The daily renewal timer never
  rotates it; it logs `NVDA Remote CA expires within a year; rerun the OpenVPN
  deploy workflow to rotate it` to `journalctl -u nvda-remote-cert-renew`
  instead. Run the workflow at least once a year so this happens in time.
- **`/etc/nvda-remote-server/ca` is lost**, for example when the Linux host is
  rebuilt.
- **An operator rotates it manually** (below).

Rerunning the workflow, the yearly server certificate renewal, a changed
private IPv4 or `vpn_cidr`, a new NVDA Remote image, a rebuilt Windows AMI, and
a VMS Portal redeploy do not change the CA.

After the CA changes, Windows VMs replace it automatically, VMS Portal shows
the new fingerprint and installer within 5 minutes, and VPN users must run the
new installer once (it removes the old course CA). Until they do, NVDA shows the
certificate prompt.

#### Rotate the CA manually

Rotate it immediately if the CA private key may have been exposed:

```bash
sudo mv /etc/nvda-remote-server/ca "/etc/nvda-remote-server/ca.retired-$(date +%F)"
```

Then rerun the OpenVPN deploy workflow right away; it creates a new CA, issues
a new server certificate, and publishes the CA. Ask every VPN user to download
and run the installer from VMS Portal again. Delete the retired directory once
the rotation is confirmed, because it still contains the old private key.

The GitHub deployment role needs permission to manage the
`nvda-remote-ca-trust` stack, its `AWS::SSM::Association`
(`ssm:CreateAssociation`, `ssm:UpdateAssociation`, `ssm:DeleteAssociation`,
`ssm:DescribeAssociation`), and its secret (`secretsmanager:CreateSecret`,
`secretsmanager:UpdateSecret`, `secretsmanager:PutSecretValue`,
`secretsmanager:DeleteSecret`, `secretsmanager:DescribeSecret`,
`secretsmanager:TagResource` on `prod/vms-portal/nvda-remote-??????`), plus
`ec2:DescribeInstances` to read the host private IPv4.

Operator checks on the Linux host:

```bash
systemctl status nvda-remote-server
sudo journalctl -u nvda-remote-server -n 100
sudo nft list chain inet openvpn_course_input input
systemctl list-timers nvda-remote-cert-renew.timer
sudo openssl x509 -in /var/lib/nvda-remote-server/server.crt -noout -ext subjectAltName -enddate
```

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
`inet openvpn_course` table when present, `inet openvpn_course_input`, and
`ip openvpn_course_nat`; never remove those rules/tables while the VPN service
is running.

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
