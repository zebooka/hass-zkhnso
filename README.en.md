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
- Retrieves tariffs and usage norms.

Data is updated once per hour. The integration creates a separate device with meter and tariff sensors for each utility account associated with the login. New addresses and sensors are discovered during updates. Configure the water heating rate separately for each address by selecting its utility account in the integration options. The former shared rate must be configured again.

## Calculating meter costs

To calculate water costs in the **Energy** dashboard, you need combined hot- and cold-water tariffs. There are two ways to create them: the integration can attempt to create combined tariff sensors automatically using the available wastewater, cold-water, and hot-water tariffs, or you can create tariff sensors manually using templates.

### Automatic combined tariffs

Automatic combined tariffs require several conditions. First, the management company must provide the following tariff options through ZKHNSO:

- For wastewater: `ВОДООТВЕДЕНИЕ` or `ОТВЕД. СТОЧ. ВОД СОИ`.
- For cold water: `ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ` or `ХВ НА СОД-Е ОБЩ.ИМУЩ.`.
- For hot water:
  - Option 1: `ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ` or `ГВ НА СОД-Е ОБЩ.ИМУЩ.`.
  - Option 2:
    - Heating: `ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ (ПОДОГРЕВ)`, `ГВ НА СОИ (ПОДОГРЕВ)`, or `ОТОПЛЕНИЕ`.
    - Heat carrier: `ГОРЯЧЕЕ ВОДОСНАБЖЕНИЕ (ТЕПЛОНОСИТ)`, `ГВ НА СОИ (ТЕПЛОНОСИТ)`, `ХОЛОДНОЕ ВОДОСНАБЖЕНИЕ`, or `ХВ НА СОД-Е ОБЩ.ИМУЩ.`.

Second, if heating tariffs are used to calculate the combined hot-water tariff, the **water heating rate** must be set in the integration settings. It can be entered manually or calculated automatically based on the [building features and hot-water system type](#thermal-energy-consumption-standard-for-heating-cold-water-in-novosibirsk-oblast).

Currently, tariffs are selected automatically in priority order.

### Combined tariffs using template sensors

You can create a template sensor in two ways: through the user interface or through `configuration.yaml`. The latter is preferable because it allows template editing, but it requires access to the **Home Assistant** file system.

#### Creating a template sensor through the user interface

1. Open **Settings → Devices & services**.
2. Open the **Helpers** tab.
3. Select `+ Create helper`.
4. Search for **Template**, then choose the **Sensor** subtype.
5. Specify the name, state template, unit of measurement (`RUB/m³`), device (**ZKHNSO**), and an availability template in advanced settings.

#### Creating a template sensor through configuration.yaml

Add the required sensors to `configuration.yaml`. Example configuration:

```yaml
template:
  - sensor:
      - name: tariff_hot_water
        unique_id: tariff_hot_water
        icon: "mdi:chart-box-outline"
        state: >
          {{ (
            (states('sensor.tariff_otved_stoch_vod_soi_1234567890') | float(0))
            + (states('sensor.tariff_gv_na_soi_teplonosit_1234567890') | float(0))
            + (states('sensor.tariff_gv_na_soi_podogrev_1234567890') | float(0))
            * 0.06528
          ) | round(2) }}
        availability: >
          {{
            states('sensor.tariff_otved_stoch_vod_soi_1234567890') not in ['unknown', 'unavailable', 'none']
            and states('sensor.tariff_gv_na_soi_teplonosit_1234567890') not in ['unknown', 'unavailable', 'none']
            and states('sensor.tariff_gv_na_soi_podogrev_1234567890') not in ['unknown', 'unavailable', 'none']
          }}
        unit_of_measurement: "RUB/m³"
      - name: tariff_cold_water
        unique_id: tariff_cold_water
        icon: "mdi:chart-box-outline"
        state: >
          {{ (
            (states('sensor.tariff_otved_stoch_vod_soi_1234567890') | float(0))
            + (states('sensor.tariff_khv_na_sod_e_obshch_imushch_1234567890') | float(0))
          ) | round(2) }}
        availability: >
          {{
            states('sensor.tariff_otved_stoch_vod_soi_1234567890') not in ['unknown', 'unavailable', 'none']
            and states('sensor.tariff_khv_na_sod_e_obshch_imushch_1234567890') not in ['unknown', 'unavailable', 'none']
          }}
        unit_of_measurement: "RUB/m³"
```

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
- New meters and tariffs are added automatically.
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
