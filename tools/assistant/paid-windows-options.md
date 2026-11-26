# Paid Windows test VMs: options (2026-11-08, prices from the Azure price list, Spot, license included unless noted)

Limits that decide this:
- Spot is 3 vCPUs per region and quota raises are refused. Mexico Central (azwin) and West US 3 (azlinux) are full.
  At most one more 2-vCPU Spot VM can run in each of East US, Central US and Belgium Central, so 3 at once.
- Stopped (deallocated) VMs use no quota, so all of them can exist and take turns.
- Windows 10/11 need the "I have eligible multi-tenant hosting rights" box ticked at Create. That needs a
  Windows Enterprise/Education E3/E5/A3/A5 per-user license. The free Windows 11 Education key from the Azure
  Education software list is not that. With the box ticked they bill at the Linux rate.
- Not checked size by size in the portal; check at Create. Only Mexico Central and West US 3 were checked.

| Option | Cheapest size / region | 24/7 per month | Kept stopped per month |
|---|---|---|---|
| Win Server 2016 DC | D2als_v6 Spot (2 vCPU, 4 GB), Central US $0.0338/h, smalldisk image + 64 GB E6 | $33.15 | $8.45 |
| Win Server 2019 DC | same | $33.15 | $8.45 |
| Win Server 2022 DC | same | $33.15 | $8.45 |
| Windows 10 22H2 (needs the license box) | D2als_v6 Spot, Central US, Linux rate $0.0168/h, 128 GB E10 | $25.51 | $13.25 |
| Windows 11 24H2 (needs the license box) | same | $25.51 | $13.25 |
| Windows 11 ARM64 (needs the license box) | D2pls_v6 Spot (Arm), Central US, Linux rate $0.015/h, 128 GB E10 | $24.20 | $13.25 |
| azwin resize to D2as_v5 Spot (nested virtualization, no CPU throttling) | Mexico Central $0.0346/h | +$5.62 (azwin $39.47 instead of $33.85) | same as now ($14.21) |

Totals:
- All six VMs created and kept stopped, run 3 at a time when needed: about $65/month standing, plus
  $0.017-0.034/h for each one while it runs.
- Without static public IPs (connect over a private network or a temporary IP): about $43/month standing.
- 24/7 for all six is not possible within the Spot quota.

Current spend for comparison: azlinux about $21 and azwin about $34 a month (azarm and azbsd are on the free
tier), so about $55 a month already. That is past the $10 budget and runs the $100 credit down in under 2 months.
