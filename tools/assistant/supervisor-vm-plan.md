# Supervisor AI VM: plan (2026-11-08, research only, nothing created)

Goal: when Claude usage runs out, a stronger open model on an on-demand Azure GPU VM supervises the local
Qwen3-Coder-30B (the assistant's llama-server on 127.0.0.1) by giving it tasks and checking its work, then turns
itself off. It never runs 24/7.

## Blocker: no GPU on Azure for Students

- Every GPU family quota is 0, Spot vCPUs are 3 per region and regional vCPUs are 6.
- On 11-08 Azure automatically refused both self-service requests (East US Spot vCPUs 3 to 8, NVadsV710v5 0 to 8).
- The support-ticket route says: "Your subscription isn't eligible for a quota increase. To request a quota
  increase, first upgrade to a Pay-As-You-Go subscription."
- So this plan only works after Blu upgrades the subscription to Pay-As-You-Go (card on file, real money), and the
  quota request is approved after that. Approval is not guaranteed. What happens to the remaining student credit
  on upgrade is not documented clearly; ask Azure support before upgrading.

## Size (Linux Spot prices, $/hour, public price list 11-08)

| Size | GPU / VRAM | Spot price | Pay-as-you-go | Quota needed |
|---|---|---|---|---|
| **NC24ads_A100_v4** | 1x A100 80 GB | **eastus 0.679**, westus3 0.746, centralus 0.843 | 3.67 | Spot: Total Regional Spot vCPUs >= 24. Regular: NCADS_A100_v4 family 24 and regional 24 |
| NV72ads_A10_v5 | 2x A10, 48 GB total | 1.205 (eastus/centralus/westus3), mexicocentral 1.33 | 6.52 | Spot vCPUs >= 72 |
| NC40ads_H100_v5 | H100 94 GB | eastus 1.418, centralus 1.587 | 6.98 | Spot vCPUs >= 40 |
| NC64as_T4_v3 | 4x T4, 64 GB total | westus3 1.234, eastus 1.357 | 4.35 | Spot vCPUs >= 64 |
| NV28adms_V710_v5 | 1x V710, 28 GB | eastus 0.444 | 2.40 | Too small for a 70B at Q4 (about 42 GB) |

Best: **NC24ads_A100_v4 Spot in East US, $0.68/h.** 80 GB fits:
- Qwen2.5-72B or Llama-3.3-70B at Q4_K_M or Q5;
- gpt-oss-120b (MXFP4, about 63 GB), a much stronger MoE that is also fast.

Run it with llama.cpp `llama-server` (CUDA), which is the same OpenAI API the assistant already uses.

## Cost per session (after an upgrade and approval)

- **A 2-hour supervision session:** 2 x $0.68 = about $1.36.
- **Fixed cost while it's off:**
  - 128 GB Standard SSD that keeps the model: about $9.60/month;
  - static public IP: $3.65/month, or no public IP if the PC connects another way;
  - together about $13/month.
- **First-time model download:** about 40-65 GB from Hugging Face. It stays on the disk afterwards.
- **Time to first token after Start:** about 3-5 minutes (boot, load the model into VRAM).

## Start and stop without device-code sign-in

What the student tenant allows (checked in the portal on 11-08):

- **Self-stop: yes.** The VM gets a system-assigned managed identity, and Blu (Owner of the subscription) gives that
  identity "Virtual Machine Contributor" on the VM itself. An idle watchdog on the VM (no API request for 20 minutes,
  plus a hard limit of 3 hours) runs `az login --identity && az vm deallocate --ids <own id>`. This needs no Entra
  app registration.
- **Service principal: no.** App registrations are blocked (the portal returns 401 on the App registrations list
  and on User settings). There is no stored SP secret.
- **`az login` in a browser on the PC: untested.** Windows `az login` uses a browser or WAM flow, not a device
  code. The same Conditional Access policy that refused the Arc device-code sign-in may or may not allow it. Test
  it once (`az login`, then `az vm start`).
- **Automation runbook + webhook: allowed (Owner, no Entra app).**
  - An Automation account with a system-assigned identity has "Virtual Machine Contributor" on the resource group,
    and a runbook does `Start-AzVM`.
  - The webhook URL contains a secret token. Blu creates it in the portal and pastes it into the PC config himself.
  - The PC's assistant starts the VM with one HTTPS POST. Automation's free tier is 500 job minutes a month.
  - This is the recommended start path. The same runbook can restart azlinux/anyps5-win after a Spot eviction.

## Flow

1. The assistant sees Claude's usage cap (claude_bridge reports the limit) and there is queued work.
2. It POSTs to the Automation webhook, which starts `anyps5-super`.
3. It waits for `ssh azsuper` (SSH key, NSG limited to the home and VPN IPs), then opens a tunnel:
   `ssh -N -L 18080:127.0.0.1:8080 azsuper`.
4. The supervisor's llama-server comes up as a systemd unit at boot with the model from the persistent disk.
5. The supervisor loop runs on the PC, in the assistant:
   - send the supervisor the task, the repo context summary and the local model's last diff and test output
     (POST /v1/chat/completions to 127.0.0.1:18080);
   - the supervisor answers with a plan or review in a fixed JSON shape:
     `{"verdict": "ok|redo|stop", "next_task": "...", "checks": ["ctest -R ...", ...], "notes": "..."}`;
   - the assistant hands next_task to Qwen3-Coder-30B (its llama-server), runs the checks offline, and repeats;
   - limits: about 30 rounds or 2 hours per session, then stop. Nothing is posted or pushed; results wait for
     Claude/Blu review, as with the pipeline drafts.
6. The VM deallocates itself after 20 minutes without requests, or when the assistant sends `POST /shutdown` to the
   watchdog, or after the 3-hour hard limit.

## What Blu decides

1. Whether to upgrade to Pay-As-You-Go at all. Without it there is no GPU, and this plan stops here.
2. If yes: file the quota request "Total Regional Spot vCPUs, East US, 24" after upgrading (the portal currently
   refuses it).
3. Create the Automation account and webhook (free tier). This is useful now for restarting evicted Spot VMs.
