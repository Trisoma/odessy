import 'package:flutter/material.dart';
import 'package:odessy_client/odessy_client.dart';
import '../../../app/theme.dart';

/// Read-only presentation of supplied reports. This widget performs no I/O.
class MonitoringDetails extends StatelessWidget {
  const MonitoringDetails(
      {super.key,
      this.host,
      this.report,
      this.loading = false,
      this.retained = false,
      this.error,
      this.demo = false,
      this.showHeader = true,
      this.onRefresh});
  final MonitoringHost? host;
  final Json? report;
  final bool loading, retained, demo, showHeader;
  final String? error;
  final VoidCallback? onRefresh;

  Map _map(Object? value) => value is Map ? value : const {};
  bool get _mismatched =>
      report != null &&
      (report!['host_id'] != host?.id || report!['schema_version'] != 1);
  bool get _restricted => host?.access != 'allowed' || host?.trust == 'revoked';
  bool get _historical =>
      retained || error != null || host?.connectivity != 'online';
  Map get _sample => _restricted || _mismatched
      ? const {}
      : report != null
          ? _map(report!['sample'])
          : _map(host?.sample);
  bool _stale(String group) =>
      _historical ||
      (report?['stale_groups'] is List &&
          (report!['stale_groups'] as List).contains(group)) ||
      _map(_sample[group])['status'] == 'stale';
  String _time(Object? raw) {
    final parsed = raw is String ? DateTime.tryParse(raw) : null;
    return parsed == null
        ? 'Observation time unavailable'
        : parsed.toUtc().toIso8601String();
  }

  String _status(Object? raw) => switch (raw) {
        'ok' => 'Reported',
        'warming_up' => 'Warming up',
        'unsupported' => 'Unsupported',
        'permission_denied' => 'Permission denied',
        'error' => 'Collector error',
        'stale' => 'Stale',
        'online' => 'Online',
        'offline' => 'Offline',
        'paired' => 'Paired',
        'local' => 'Local',
        'revoked' => 'Revoked',
        'allowed' => 'Allowed',
        'denied' => 'Denied',
        'disabled' => 'Disabled',
        'healthy' => 'Healthy',
        'unhealthy' => 'Unhealthy',
        _ => 'Unknown',
      };
  num? _number(Object? raw, {num? maximum}) => raw is num &&
          raw.isFinite &&
          raw >= 0 &&
          (maximum == null || raw <= maximum)
      ? raw
      : null;
  String _percent(Object? raw) {
    final number = _number(raw, maximum: 100);
    return number == null ? 'Unavailable' : '${number.toStringAsFixed(1)}%';
  }

  String _bytes(Object? raw) {
    final number = _number(raw);
    return number == null
        ? 'Unavailable'
        : '${(number / (1024 * 1024 * 1024)).toStringAsFixed(2)} GiB';
  }

  bool _readable(Map group) =>
      group['status'] == 'ok' || group['status'] == 'stale';

  @override
  Widget build(BuildContext context) => LayoutBuilder(builder: (context, box) {
        final inset = box.maxWidth < 600 ? 16.0 : 24.0;
        return SizedBox(
            height: box.maxHeight.isFinite ? box.maxHeight : null,
            child: SingleChildScrollView(
                key: const Key('monitoring-detail-scroll'),
                padding: EdgeInsets.all(inset),
                child: Align(
                    alignment: Alignment.topCenter,
                    child: ConstrainedBox(
                        constraints: const BoxConstraints(maxWidth: 1040),
                        child: Column(
                            crossAxisAlignment: CrossAxisAlignment.stretch,
                            children: [
                              if (host == null) ...[
                                Text(
                                    loading
                                        ? 'Loading devices'
                                        : 'No device selected',
                                    style:
                                        Theme.of(context).textTheme.titleLarge),
                                const SizedBox(height: 12),
                                Text(error ??
                                    'Choose a device to inspect its reported state.'),
                              ] else ...[
                                if (showHeader) ...[
                                  Semantics(
                                      header: true,
                                      child: Text(host!.name,
                                          style: Theme.of(context)
                                              .textTheme
                                              .titleLarge)),
                                  const SizedBox(height: 8),
                                ],
                                SelectableText(host!.id,
                                    style:
                                        Theme.of(context).textTheme.bodySmall),
                                const SizedBox(height: 12),
                                Text(demo
                                    ? 'DEMO · illustrative monitoring data'
                                    : 'Read-only device monitoring'),
                                const SizedBox(height: 8),
                                if (showHeader)
                                  const Text(
                                      'Inspecting this device does not change the conversation coordinator or execution target.'),
                                const SizedBox(height: 16),
                                if (_historical)
                                  _notice(context, 'Historical report',
                                      'Retained readings are not a current device health report.'),
                                if (_mismatched)
                                  _notice(context, 'Report unavailable',
                                      'The supplied report does not match this device or supported format.'),
                                if (error != null)
                                  _notice(context, 'Monitoring needs attention',
                                      error!),
                                _card(context, 'Device state', [
                                  _row(context, 'Connection',
                                      '${_status(host!.connectivity)}${_historical ? ' · last report' : ''}'),
                                  _row(context, 'Trust',
                                      '${_status(host!.trust)}${_historical ? ' · last report' : ''}'),
                                  _row(context, 'Telemetry access',
                                      '${_status(host!.access)}${_historical ? ' · last report' : ''}'),
                                ]),
                                if (_restricted)
                                  _notice(context, 'Telemetry unavailable',
                                      'No readings are displayed for this access or trust state.')
                                else if (!_mismatched) ...[
                                  _metrics(context),
                                  _gpu(context),
                                  _services(context),
                                ],
                              ],
                              if (loading && report == null)
                                const Padding(
                                    padding: EdgeInsets.symmetric(vertical: 12),
                                    child: LinearProgressIndicator(
                                        semanticsLabel:
                                            'Loading monitoring report')),
                              Align(
                                  alignment: Alignment.centerLeft,
                                  child: OutlinedButton.icon(
                                      onPressed: loading && report == null
                                          ? null
                                          : onRefresh,
                                      icon: const Icon(Icons.refresh, size: 18),
                                      label: const Text('Refresh monitoring'))),
                            ])))));
      });

  Widget _metrics(BuildContext context) {
    final cpu = _map(_sample['cpu']), memory = _map(_sample['memory']);
    return LayoutBuilder(builder: (context, box) {
      final wide = box.maxWidth >= 680 &&
          MediaQuery.textScalerOf(context).scale(14) <= 20;
      final cards = [
        _card(context, 'CPU', [
          Text(
              _readable(cpu)
                  ? _percent(cpu['utilization_percent'])
                  : 'Unavailable',
              style: Theme.of(context).textTheme.titleLarge),
          _quality(context, 'cpu', cpu),
        ]),
        _card(context, 'Memory', [
          _row(context, 'Used',
              _readable(memory) ? _bytes(memory['used_bytes']) : 'Unavailable'),
          _row(
              context,
              'Total',
              _readable(memory)
                  ? _bytes(memory['total_bytes'])
                  : 'Unavailable'),
          _quality(context, 'memory', memory),
        ]),
      ];
      return wide
          ? Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Expanded(child: cards[0]),
              const SizedBox(width: 16),
              Expanded(child: cards[1]),
            ])
          : Column(children: cards);
    });
  }

  Widget _quality(BuildContext context, String name, Map group) =>
      Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        const SizedBox(height: 12),
        Text('Collector: ${_status(group['status'])}'),
        if (_stale(name)) const Text('Historical / stale reading'),
        const SizedBox(height: 4),
        Text(_time(group['observed_at'] ?? _sample['observed_at']),
            style: Theme.of(context).textTheme.bodySmall),
      ]);

  Widget _gpu(BuildContext context) {
    final gpu = _map(_sample['gpu']);
    final devices = gpu['devices'] is List
        ? (gpu['devices'] as List).whereType<Map>().toList()
        : const <Map>[];
    return _card(context, 'GPU', [
      if (!_readable(gpu) || devices.isEmpty)
        const Text('GPU readings unavailable'),
      if (_readable(gpu))
        for (final device in devices) ...[
          Text(device['id'] is String ? device['id'] as String : 'GPU device'),
          _row(context, 'Utilization', _percent(device['utilization_percent'])),
          _row(context, 'Memory used', _bytes(device['memory_used_bytes'])),
        ],
      _quality(context, 'gpu', gpu),
    ]);
  }

  Widget _services(BuildContext context) {
    final services = report?['services'] is List
        ? (report!['services'] as List).whereType<Map>().toList()
        : const <Map>[];
    return _card(context, 'Runtime services', [
      if (services.isEmpty) const Text('No service readiness report available'),
      for (final service in services) ...[
        _row(
            context,
            service['name'] is String ? service['name'] as String : 'Service',
            '${_status(service['status'])}${_historical ? ' · last report' : ''}'),
        Text(_time(service['observed_at']),
            style: Theme.of(context).textTheme.bodySmall),
        if (service['reason'] is String) Text(service['reason'] as String),
        const SizedBox(height: 8),
      ],
    ]);
  }

  Widget _notice(BuildContext context, String title, String text) =>
      _card(context, title, [Text(text)]);

  Widget _card(BuildContext context, String title, List<Widget> children) =>
      Padding(
          padding: const EdgeInsets.only(bottom: 16),
          child: Card(
              child: Padding(
                  padding: const EdgeInsets.all(OdessyTokens.spaceLarge),
                  child: Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        Text(title,
                            style: Theme.of(context).textTheme.titleMedium),
                        const SizedBox(height: 12),
                        ...children,
                      ]))));

  Widget _row(BuildContext context, String label, String value) => Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: LayoutBuilder(builder: (context, box) {
        final heading =
            Text(label, style: Theme.of(context).textTheme.bodySmall);
        final content = SelectableText(value);
        if (box.maxWidth < 420) {
          return Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [heading, const SizedBox(height: 4), content]);
        }
        return Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          SizedBox(width: 160, child: heading),
          const SizedBox(width: 12),
          Expanded(child: content),
        ]);
      }));
}
