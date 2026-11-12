[English](/README.en.md) | [Русский](/README.md)

# ZKHNSO Integration for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5)](https://hacs.xyz)
[![Release](https://img.shields.io/github/v/release/zebooka/hass-zkhnso?label=release&color=blue)](https://github.com/zebooka/hass-zkhnso/releases/latest)
![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.12%2B-blue?logo=home-assistant)
[![License](https://img.shields.io/github/license/zebooka/hass-zkhnso?color=green)](https://github.com/zebooka/hass-zkhnso/blob/master/LICENSE)
![Custom Integration](https://img.shields.io/badge/Custom%20Integration-orange)
[![Sponsor on Cloudtips](https://img.shields.io/badge/donate-cloudtips-FFDD2D.svg)](https://pay.cloudtips.ru/p/34ce579c)
[![Русский язык](https://img.shields.io/badge/Russian%20language-ДА-red)](https://en.wikipedia.org/wiki/Alexander_Pushkin)

<img src="https://github.com/zebooka/hass-zkhnso/blob/master/custom_components/zkhnso/brand/logo@2x.png?raw=true" alt="ZKHNSO" width="128"/>

An integration for the ЖКХНСО.рф housing and utility service in Home Assistant. It provides meter and tariff information for Novosibirsk Oblast, Russian Federation.

## Installation

### HACS

[![Open your Home Assistant instance and show the repository in the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=zebooka&repository=hass-zkhnso&category=integration)

1. Open **HACS** in Home Assistant and search for **ЖКХНСО** (or use the button above).
2. Open **ЖКХНСО**, select **Download**, and confirm the action.
3. Restart Home Assistant, then continue with [Configuration](#configuration).

### HACS: Custom repository

1. In HACS, open **⋮ → Custom repositories**.
2. Add `https://github.com/zebooka/hass-zkhnso` with type **Integration**.
3. Find **ЖКХНСО** and install the integration.
4. Restart Home Assistant.
5. Add the integration in **Settings → Devices & services**.

### Manual installation

1. Copy the `zkhnso` directory to your Home Assistant `custom_components` directory.
2. Restart Home Assistant.
3. Add the integration in **Settings → Devices & services**.

## Configuration

This integration is configured through the Home Assistant user interface:

1. Open **Settings → Devices & services**.
2. Select `+ Add integration`.
3. Search for **ЖКХНСО**.
4. Enter your account credentials.

Use the username and password from your ZKHNSO personal account. Credentials are validated before the integration entry is created. If the password changes, Home Assistant prompts you to reauthenticate.

Settings from previous versions are incompatible with the new version: remove the old integration entry and configure it again. Previously created devices and entities may differ from the new ones.

## Features

- Retrieves active meters, including serial number, meter type, next verification date, current reading, and the date of the most recent reading submission.
- Retrieves archived meters.
- Retrieves tariffs and usage norms.
- Retrieves the utility account number, address, and management company name and address.
- Emits an event when a meter's verification date approaches.

Data is updated once per hour. The integration creates a separate device with meter and tariff sensors for each utility account. Select the account and tariffs in the integration options; if using a heat-transfer fluid tariff, also configure the heating tariff and rate.

### Meter verification event

The `zkhnso_meter_verification_alert` event fires when an enabled active meter sensor is 30 calendar days or less away from its verification date, including overdue dates. It fires at most once per meter and verification date, including across Home Assistant restarts. A changed date can trigger a new event. Archived and disabled meters are not checked.

Event data contains `entry_id`, `entity_id`, `account_id`, `meter_id`, `serial_number`, `name`, `next_verification_date` and `days_left`, which is negative for overdue dates; it does not contain an address. Home Assistant's event bus does not persist events, so an automation not yet loaded at startup may miss an alert. The sent marker is saved before firing, so a failure between these operations can also result in a missed event.

```yaml
automation:
  - alias: Meter verification
    triggers:
      - trigger: event
        event_type: zkhnso_meter_verification_alert
    actions:
      - action: persistent_notification.create
        data:
          title: Meter verification is approaching
          message: >-
            {{ trigger.event.data.name }} #{{ trigger.event.data.serial_number }},
            Acct.No.: #{{ trigger.event.data.account_id }}:
            verification on {{ trigger.event.data.next_verification_date | as_timestamp |
            timestamp_custom('%d.%m.%Y') }}
```

## Calculating meter costs

To calculate water costs in the **Energy** dashboard, combined tariffs are required because water costs are calculated using several tariffs. Configure combined tariffs in the integration options.

Calculating combined tariffs requires several conditions. First, the management company must provide the following tariff options through ZKHNSO:

- For wastewater: `ВОДООТВЕДЕНИЕ` or `ОТВЕД. СТОЧ. ВОД СОИ`.
- For cold water: `ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ` or `ХВ НА СОД-Е ОБЩ.ИМУЩ.`.
- For hot water:
  - Option 1: `ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ` or `ГВ НА СОД-Е ОБЩ.ИМУЩ.`.
  - Option 2:
    - Heating: `ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ (ПОДОГРЕВ)`, `ГВ НА СОИ (ПОДОГРЕВ)`, or `ОТОПЛЕНИЕ`.
    - Heat carrier: `ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ (ТЕПЛОНОСИТ)`, `ГВ НА СОИ (ТЕПЛОНОСИТ)`, `ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ`, or `ХВ НА СОД-Е ОБЩ.ИМУЩ.`.

Second, if heating tariffs are used to calculate the combined hot-water tariff, the **water heating rate** must be set in the integration settings. It can be entered manually or calculated automatically based on the [building features and hot-water system type](#thermal-energy-consumption-standard-for-heating-cold-water-in-novosibirsk-oblast).

Select tariffs for each account in the integration options. The automatic selection used in version `1.1.x` is no longer available, so tariffs must be selected again. An empty selection or an unavailable selected tariff makes the corresponding combined tariff unknown.

### Thermal energy consumption standard for heating cold water in Novosibirsk Oblast

Set the rate used to calculate the combined hot-water tariff in the configuration form according to your water supply
system. It is established by legislation of Novosibirsk Oblast and, at the time this README was written in 2026, is:

<table>
<thead>
<tr>
<th rowspan="3">Apartment building or residential building construction features</th>
<th colspan="3">Thermal energy consumption standards for heating cold water to provide the hot-water utility service, Gcal per 1 m³</th>
</tr>
<tr>
<th colspan="2">Closed hot-water supply system</th>
<th>Open heat-supply (hot-water supply) system</th>
</tr>
<tr>
<th>With an external hot-water supply network</th>
<th>Without an external hot-water supply network</th>
<th>Without an external hot-water supply network</th>
</tr>
</thead>
<tbody>
<tr><td>Uninsulated risers and heated towel rails</td><td>0.06779</td><td>0.06528</td><td>0.06528</td></tr>
<tr><td>Insulated risers and heated towel rails</td><td>0.06277</td><td>0.06026</td><td>0.06026</td></tr>
<tr><td>Uninsulated risers without heated towel rails</td><td>0.06277</td><td>0.06026</td><td>0.06026</td></tr>
<tr><td>Insulated risers without heated towel rails</td><td>0.05775</td><td>0.05524</td><td>0.05524</td></tr>
</tbody>
</table>

- **Closed hot-water supply system:** — The heating and hot-water circuits are separate. Hot water is heated through a heat exchanger. This is typical of newer buildings. With an external hot-water network, water is heated outside the building at a heating station serving a group of buildings. Without an external network, water is heated at a heating station inside the building.
- **Open hot-water supply system:** — The same heat carrier is supplied to both the heating and hot-water systems. This is typical of older buildings.
- **Insulated risers:** — Hot-water supply pipes covered with a layer of thermal insulation.

## Limitations and troubleshooting

- The integration works only with ZKHNSO personal accounts in Novosibirsk Oblast.
- If the ZKHNSO website is unavailable, Home Assistant retries the connection automatically.
- New meters, tariffs and accounts/addresses are added automatically.
- To remove the integration, open its page under **Settings → Devices & services** and select **Delete**.

## Support

Report bugs and suggestions through the [GitHub repository](https://github.com/zebooka/hass-zkhnso).

## License

This project is distributed under the MIT License.

## Thanks

- novodim
- slavijan
- GPT-5.6 Terra
- GPT-6.1 Terra
