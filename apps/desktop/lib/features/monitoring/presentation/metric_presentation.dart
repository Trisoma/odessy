import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';

/// One labelled metric value with an explicit unit.
class MetricFact {
  const MetricFact(this.label, this.value, {this.unit = ''});
  final String label;
  final String value;
  final String unit;

  String get display => unit.isEmpty ? value : '$value $unit';
}

/// A device entry (disk, interface, GPU, sensor, rail) and its facts.
class MetricDevice {
  const MetricDevice(this.title, this.facts, {this.note = ''});
  final String title;
  final List<MetricFact> facts;
  final String note;
}

/// A whole metric group rendered from the monitoring-v1 sample contract.
class GroupSummary {
  const GroupSummary({
    required this.group,
    required this.title,
    required this.status,
    this.reason = '',
    this.observedAt,
    this.facts = const [],
    this.devices = const [],
    this.raw,
  });

  final String group;
  final String title;
  final String status;
  final String reason;
  final String? observedAt;
  final List<MetricFact> facts;
  final List<MetricDevice> devices;

  /// The untouched backend payload, shown only behind diagnostic details.
  final dynamic raw;

  /// Collector states that carry no trustworthy values.
  bool get unavailable =>
      status == 'unsupported' ||
      status == 'permission_denied' ||
      status == 'error' ||
      status == 'warming_up';

  /// True only when the collector reported usable numbers.
  bool get hasValues =>
      facts.isNotEmpty || devices.isNotEmpty;
}

String formatBytes(num value) {
  final v = value.abs();
  if (v < 1024) return '${value.round()} B';
  const units = ['KiB', 'MiB', 'GiB', 'TiB', 'PiB'];
  var scaled = value / 1024;
  var unit = 0;
  while (scaled.abs() >= 1024 && unit < units.length - 1) {
    scaled /= 1024;
    unit++;
  }
  return '${scaled.toStringAsFixed(1)} ${units[unit]}';
}

String formatRate(num value) => '${formatBytes(value)}/s';

String formatPercent(num value) => '${value.toStringAsFixed(1)}%';

String formatNumber(num value) =>
    value == value.roundToDouble() ? '${value.round()}' : value.toStringAsFixed(2);

String formatTemperature(num value) => '${value.toStringAsFixed(1)} °C';

String formatWatts(num value) => '${value.toStringAsFixed(1)} W';

/// Human wording for collector status; raw enum stays available in details.
String statusLabel(String status) => switch (status) {
      'ok' => 'Current',
      'warming_up' => 'Warming up',
      'unsupported' => 'Unsupported',
      'permission_denied' => 'Permission denied',
      'error' => 'Collection failed',
      'stale' => 'Stale',
      _ => 'Unknown',
    };

String _observed(Map value) {
  final raw = value['observed_at'];
  final parsed = raw is String ? DateTime.tryParse(raw) : null;
  if (parsed == null) return '';
  final local = parsed.toLocal();
  String two(int v) => v.toString().padLeft(2, '0');
  return '${two(local.hour)}:${two(local.minute)}:${two(local.second)}';
}

List<MetricFact> _factsFrom(
    Map value, Map<String, (String label, String Function(num) format)> fields,
    {bool skipZero = false}) {
  final facts = <MetricFact>[];
  for (final entry in fields.entries) {
    final raw = value[entry.key];
    if (raw is! num || !raw.isFinite || raw < 0) continue;
    if (skipZero && raw == 0) continue;
    facts.add(MetricFact(entry.value.$1, entry.value.$2(raw)));
  }
  return facts;
}

List<MetricDevice> _devicesFrom(dynamic list, String titleKey,
    Map<String, (String label, String Function(num) format)> fields) {
  if (list is! List) return const [];
  final out = <MetricDevice>[];
  for (final entry in list) {
    if (entry is! Map) continue;
    final title = '${entry[titleKey] ?? entry['id'] ?? 'unnamed'}';
    final facts = _factsFrom(entry, fields);
    if (facts.isEmpty) continue;
    out.add(MetricDevice(title, facts,
        note: entry['domain'] is String ? '${entry['domain']} rail' : ''));
  }
  return out;
}

const Map<String, String> groupTitles = {
  'cpu': 'CPU',
  'memory': 'Memory',
  'disks': 'Storage',
  'disk_io': 'Disk throughput',
  'network': 'Network',
  'gpu': 'GPU',
  'thermal': 'Temperature',
  'power': 'Power',
};

/// Builds a presentation summary for one sample group. Values that the backend
/// did not report stay absent; nothing is replaced by a zero.
GroupSummary summariseGroup(String group, dynamic value, {bool stale = false}) {
  final title = groupTitles[group] ?? group;
  if (value is! Map) {
    return GroupSummary(
        group: group,
        title: title,
        status: 'unknown',
        reason: 'Not reported',
        raw: value);
  }
  final status = '${value['status'] ?? 'unknown'}';
  final reason = '${value['reason'] ?? ''}';
  final observed = _observed(value);

  final facts = <MetricFact>[];
  var devices = <MetricDevice>[];

  switch (group) {
    case 'cpu':
      facts.addAll(_factsFrom(value, {
        'utilization_percent': ('Utilisation', formatPercent),
        'load_1': ('Load 1 min', formatNumber),
        'load_5': ('Load 5 min', formatNumber),
        'load_15': ('Load 15 min', formatNumber),
        'core_count': ('Cores', formatNumber),
      }));
    case 'memory':
      facts.addAll(_factsFrom(value, {
        'utilization_percent': ('Utilisation', formatPercent),
        'used_bytes': ('Used', formatBytes),
        'total_bytes': ('Total', formatBytes),
        'available_bytes': ('Available', formatBytes),
        'shared_bytes': ('Shared', formatBytes),
        'swap_used_bytes': ('Swap used', formatBytes),
        'swap_total_bytes': ('Swap total', formatBytes),
      }));
    case 'disks':
      devices = _devicesFrom(value['devices'], 'id', {
        'used_bytes': ('Used', formatBytes),
        'total_bytes': ('Size', formatBytes),
        'available_bytes': ('Available', formatBytes),
        'utilization_percent': ('Utilisation', formatPercent),
      });
    case 'disk_io':
      devices = _devicesFrom(value['devices'], 'id', {
        'read_bytes_per_second': ('Read', formatRate),
        'write_bytes_per_second': ('Write', formatRate),
      });
    case 'network':
      devices = _devicesFrom(value['devices'], 'id', {
        'rx_bytes_per_second': ('Receive', formatRate),
        'tx_bytes_per_second': ('Transmit', formatRate),
        'rx_bytes': ('Received', formatBytes),
        'tx_bytes': ('Transmitted', formatBytes),
      });
    case 'gpu':
      devices = _devicesFrom(value['devices'], 'id', {
        'utilization_percent': ('Utilisation', formatPercent),
        'memory_used_bytes': ('Memory used', formatBytes),
        'memory_total_bytes': ('Memory total', formatBytes),
        'temperature_celsius': ('Temperature', formatTemperature),
        'power_watts': ('Power', formatWatts),
      });
    case 'thermal':
      devices = _devicesFrom(value['devices'], 'id', {
        'temperature_celsius': ('Temperature', formatTemperature),
      });
    case 'power':
      devices = _devicesFrom(value['devices'], 'id', {
        'watts': ('Power', formatWatts),
      });
  }

  final truncation = value['truncated'] == true
      ? 'The backend truncated this list to its per-host cap.'
      : '';

  return GroupSummary(
    group: group,
    title: title,
    status: stale && status == 'ok' ? 'stale' : status,
    reason: truncation.isNotEmpty ? truncation : reason,
    observedAt: observed.isEmpty ? null : observed,
    facts: facts,
    devices: devices,
    raw: value,
  );
}

/// Pretty-prints the raw payload for diagnostic details.
String rawPayload(dynamic value) =>
    const JsonEncoder.withIndent('  ').convert(value);

class MetricCard extends StatefulWidget {
  const MetricCard({super.key, required this.summary});
  final GroupSummary summary;

  @override
  State<MetricCard> createState() => _MetricCardState();
}

class _MetricCardState extends State<MetricCard> {
  bool open = false;

  @override
  Widget build(BuildContext context) {
    final s = widget.summary;
    final theme = Theme.of(context);
    final muted = theme.colorScheme.onSurfaceVariant;
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Expanded(
                child: Text(s.title,
                    style: theme.textTheme.titleSmall,
                    overflow: TextOverflow.ellipsis)),
            Text(statusLabel(s.status),
                style: TextStyle(
                    fontSize: 11,
                    color: s.status == 'ok'
                        ? theme.colorScheme.primary
                        : muted)),
            if (s.observedAt != null) ...[
              const SizedBox(width: 8),
              Text('· ${s.observedAt}',
                  style: TextStyle(fontSize: 11, color: muted)),
            ],
          ]),
          if (s.reason.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(s.reason,
                  style: TextStyle(fontSize: 11, color: muted)),
            ),
          if (s.unavailable && !s.hasValues)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text('No values reported. This is not zero usage.',
                  style: TextStyle(fontSize: 12, color: muted)),
            ),
          if (s.facts.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Wrap(spacing: 20, runSpacing: 8, children: [
                for (final fact in s.facts) _FactTile(fact: fact),
              ]),
            ),
          for (final device in s.devices)
            Padding(
              padding: const EdgeInsets.only(top: 10),
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(device.title,
                        style: const TextStyle(fontSize: 12),
                        overflow: TextOverflow.ellipsis),
                    if (device.note.isNotEmpty)
                      Text(device.note,
                          style: TextStyle(fontSize: 10, color: muted)),
                    Padding(
                      padding: const EdgeInsets.only(top: 4),
                      child: Wrap(spacing: 20, runSpacing: 6, children: [
                        for (final fact in device.facts)
                          _FactTile(fact: fact, compact: true),
                      ]),
                    ),
                  ]),
            ),
          const SizedBox(height: 6),
          TextButton(
            style: TextButton.styleFrom(
                padding: EdgeInsets.zero,
                minimumSize: const Size(0, 32),
                tapTargetSize: MaterialTapTargetSize.shrinkWrap),
            onPressed: () => setState(() => open = !open),
            child: Text(open ? 'Hide payload' : 'Show payload',
                style: const TextStyle(fontSize: 11)),
          ),
          if (open)
            SelectableText(rawPayload(s.raw),
                style: const TextStyle(fontSize: 11, fontFamily: 'monospace')),
        ]),
      ),
    );
  }
}

class _FactTile extends StatelessWidget {
  const _FactTile({required this.fact, this.compact = false});
  final MetricFact fact;
  final bool compact;

  @override
  Widget build(BuildContext context) {
    final muted = Theme.of(context).colorScheme.onSurfaceVariant;
    return Semantics(
      label: '${fact.label}: ${fact.display}',
      excludeSemantics: true,
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(fact.label, style: TextStyle(fontSize: 10, color: muted)),
        Text(fact.display,
            style: TextStyle(
                fontSize: compact ? 12 : 15,
                fontWeight: FontWeight.w600)),
      ]),
    );
  }
}

/// Convenience for tests and callers that hold a raw monitoring payload.
GroupSummary summariseSampleGroup(Json? sample, String group,
        {bool stale = false}) =>
    summariseGroup(group, sample?[group], stale: stale);
