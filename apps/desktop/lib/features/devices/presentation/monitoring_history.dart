import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:odessy_client/odessy_client.dart';

/// Presentation of an externally supplied monitoring-v1 history response.
/// No queries, clients, timers, credentials or observation aggregation live here.
class MonitoringHistory extends StatefulWidget {
  const MonitoringHistory(
      {super.key,
      this.host,
      this.report,
      this.loading = false,
      this.retained = false,
      this.demo = false,
      this.error,
      this.degradation,
      this.onRefresh});
  final MonitoringHost? host;
  final Json? report;
  final bool loading, retained, demo;
  final String? error, degradation;
  final VoidCallback? onRefresh;

  @override
  State<MonitoringHistory> createState() => _MonitoringHistoryState();
}

class _MonitoringHistoryState extends State<MonitoringHistory> {
  String? selectedSeries;
  DateTime? selectedAt;
  bool plotFocused = false;
  late Object scope;
  Object get currentScope => (
        widget.host?.id,
        widget.report?['from'],
        widget.report?['to'],
        widget.report?['step_seconds']
      );
  @override
  void initState() {
    super.initState();
    scope = currentScope;
  }

  @override
  void didUpdateWidget(covariant MonitoringHistory oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (scope != currentScope) {
      scope = currentScope;
      selectedSeries = null;
      selectedAt = null;
    }
  }

  @override
  Widget build(BuildContext context) {
    final host = widget.host;
    final restricted =
        host != null && (host.access != 'allowed' || host.trust == 'revoked');
    final history = host == null || restricted || widget.report == null
        ? null
        : _History.read(widget.report!, host.id);
    final degraded = widget.degradation?.trim().isNotEmpty == true;
    final retained = widget.retained ||
        widget.error != null ||
        widget.loading ||
        (host != null && host.connectivity != 'online');
    return LayoutBuilder(builder: (context, box) {
      return SizedBox(
          height: box.maxHeight.isFinite ? box.maxHeight : null,
          child: SingleChildScrollView(
              key: const Key('monitoring-history-scroll'),
              padding: EdgeInsets.all(box.maxWidth < 600 ? 16 : 24),
              child: Align(
                  alignment: Alignment.topCenter,
                  child: ConstrainedBox(
                      constraints: const BoxConstraints(maxWidth: 1040),
                      child: Column(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                            Semantics(
                                header: true,
                                child: Text('Monitoring history',
                                    style: Theme.of(context)
                                        .textTheme
                                        .titleLarge)),
                            if (host != null) ...[
                              const SizedBox(height: 8),
                              SelectableText('${host.name} · ${host.id}'),
                            ],
                            const SizedBox(height: 8),
                            Text(widget.demo
                                ? 'DEMO · illustrative history data'
                                : 'Read-only reported history'),
                            const Text(
                                'History does not show current device health or change the execution target.'),
                            if (retained && widget.report != null)
                              _notice('Retained history',
                                  'This is the last supplied response, not a fresh monitoring report.'),
                            if (degraded)
                              _notice('History storage degraded',
                                  widget.degradation!),
                            if (widget.error != null)
                              _notice('History needs attention', widget.error!),
                            if (host == null)
                              _notice(
                                  widget.loading
                                      ? 'Loading history'
                                      : 'No device selected',
                                  'Choose a device to inspect its reported history.')
                            else if (restricted)
                              _notice('History unavailable',
                                  'No readings are displayed for this access or trust state.')
                            else if (widget.report == null)
                              _notice(
                                  widget.loading
                                      ? 'Loading history'
                                      : 'History unavailable',
                                  'No history response has been supplied.')
                            else if (history?.problem != null)
                              _notice('History report unavailable',
                                  history!.problem!)
                            else if (history != null) ...[
                              const SizedBox(height: 16),
                              _field('Requested range (UTC)',
                                  '${history.from!.toIso8601String()}\nto ${history.to!.toIso8601String()}'),
                              _field('Reported step',
                                  '${history.step} seconds per bucket'),
                              const Text(
                                  'Whole buckets may overlap the requested range. Values and ranges are reported aggregates; no interpolation is applied.'),
                              if (history.unsupported > 0)
                                _notice('Unsupported series',
                                    '${history.unsupported} series not plotted. No units are inferred.'),
                              if (history.series.isEmpty)
                                _notice('No supported history series',
                                    'No CPU, memory or GPU series is available in this response.')
                              else
                                _series(context, history, degraded),
                            ],
                            if (widget.loading)
                              const Padding(
                                  padding: EdgeInsets.symmetric(vertical: 12),
                                  child: LinearProgressIndicator(
                                      semanticsLabel:
                                          'Loading monitoring history')),
                            const SizedBox(height: 16),
                            Align(
                                alignment: Alignment.centerLeft,
                                child: OutlinedButton.icon(
                                    onPressed: widget.loading ||
                                            restricted ||
                                            host == null
                                        ? null
                                        : widget.onRefresh,
                                    icon: const Icon(Icons.refresh),
                                    label: const Text('Refresh history'))),
                          ])))));
    });
  }

  Widget _notice(String heading, String text) => Card(
      child: Padding(
          padding: const EdgeInsets.all(16),
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Text(heading, style: Theme.of(context).textTheme.titleSmall),
            const SizedBox(height: 6),
            Text(text),
          ])));

  Widget _field(String label, String value) => Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(label, style: Theme.of(context).textTheme.labelMedium),
        const SizedBox(height: 4),
        SelectableText(value),
      ]));

  Widget _series(BuildContext context, _History history, bool degraded) {
    final series = history.series.firstWhere((s) => s.name == selectedSeries,
        orElse: () => history.series.first);
    var index = series.points.indexWhere((p) => p.at == selectedAt);
    if (index < 0) index = series.points.length - 1;
    void select(int next) {
      if (next >= 0 && next < series.points.length) {
        setState(() => selectedAt = series.points[next].at);
      }
    }

    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      const SizedBox(height: 16),
      InputDecorator(
          decoration: const InputDecoration(labelText: 'History metric'),
          child: DropdownButtonHideUnderline(
              child: DropdownButton<String>(
                  key: const Key('history-metric'),
                  isExpanded: true,
                  value: series.name,
                  items: [
                    for (final metric in history.series)
                      DropdownMenuItem(
                          value: metric.name,
                          child: Text(metric.metric.label,
                              overflow: TextOverflow.ellipsis))
                  ],
                  onChanged: (value) => setState(() {
                        selectedSeries = value;
                        selectedAt = null;
                      })))),
      const SizedBox(height: 8),
      SelectableText('${series.metric.label} · ${series.metric.unit}'),
      if (series.invalid > 0)
        _notice('Invalid buckets',
            '${series.invalid} invalid buckets are not plotted. Unplaceable timestamps disable connecting lines.'),
      if (series.problem != null)
        _notice('Series unavailable', series.problem!)
      else if (series.points.isEmpty)
        _notice(
            degraded
                ? 'History may be incomplete'
                : series.returned > 0
                    ? 'No valid bucket timestamps'
                    : 'No returned buckets',
            degraded
                ? 'Storage is degraded; an empty response is not evidence of zero activity.'
                : series.returned > 0
                    ? 'The supplied buckets cannot be placed on a time axis.'
                    : 'No observations were returned for this series. Missing readings are not zero.')
      else ...[
        const SizedBox(height: 12),
        Text(
            '${series.points.where((p) => p.value != null).length} plotted of ${series.returned} returned buckets'),
        Text(
            'Vertical scale: 0 to ${_chartMaximum(series).toStringAsFixed(series.metric.bytes ? 2 : 1)} ${series.metric.unit}'),
        const Text(
            'Dots: reported values. Whiskers: reported min/max. Hollow dots: partial coverage. Lines connect only adjacent fully covered buckets.'),
        const SizedBox(height: 12),
        CallbackShortcuts(
            bindings: {
              const SingleActivator(LogicalKeyboardKey.arrowLeft): () =>
                  select(index - 1),
              const SingleActivator(LogicalKeyboardKey.arrowRight): () =>
                  select(index + 1),
            },
            child: Focus(
                onFocusChange: (value) {
                  if (mounted) setState(() => plotFocused = value);
                },
                child: Semantics(
                    label:
                        '${series.metric.label} history plot. ${series.returned} returned buckets. Use the previous and next controls for exact values.',
                    child: LayoutBuilder(builder: (context, box) {
                      final start =
                          series.points.first.at.isBefore(history.from!)
                              ? series.points.first.at
                              : history.from!;
                      final end = series.points.last.at.isAfter(history.to!)
                          ? series.points.last.at
                          : history.to!;
                      return Column(children: [
                        SizedBox(
                            height: 190,
                            child: GestureDetector(
                                onTapDown: (tap) {
                                  final fraction = ((tap.localPosition.dx - 8) /
                                          math.max(1, box.maxWidth - 16))
                                      .clamp(0.0, 1.0);
                                  final target = start.add(Duration(
                                      microseconds: (end
                                                  .difference(start)
                                                  .inMicroseconds *
                                              fraction)
                                          .round()));
                                  var nearest = 0;
                                  for (var i = 1;
                                      i < series.points.length;
                                      i++) {
                                    if (series.points[i].at
                                            .difference(target)
                                            .abs() <
                                        series.points[nearest].at
                                            .difference(target)
                                            .abs()) {
                                      nearest = i;
                                    }
                                  }
                                  select(nearest);
                                },
                                child: CustomPaint(
                                    key: const Key('history-plot'),
                                    size: Size(box.maxWidth, 190),
                                    painter: _HistoryPainter(
                                        series,
                                        start,
                                        end,
                                        history.step!,
                                        index,
                                        Theme.of(context).colorScheme,
                                        plotFocused)))),
                        Row(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Expanded(
                                  child: Text(start
                                      .toIso8601String()
                                      .replaceFirst('T', '\n'))),
                              const SizedBox(width: 16),
                              Expanded(
                                  child: Text(
                                      end
                                          .toIso8601String()
                                          .replaceFirst('T', '\n'),
                                      textAlign: TextAlign.right)),
                            ]),
                      ]);
                    })))),
        const SizedBox(height: 8),
        const Text('Select a dot, or focus the plot and use Left / Right.'),
        Wrap(spacing: 12, runSpacing: 8, children: [
          OutlinedButton.icon(
              onPressed: index > 0 ? () => select(index - 1) : null,
              icon: const Icon(Icons.chevron_left),
              label: const Text('Previous bucket')),
          OutlinedButton.icon(
              onPressed: index + 1 < series.points.length
                  ? () => select(index + 1)
                  : null,
              icon: const Icon(Icons.chevron_right),
              label: const Text('Next bucket')),
        ]),
        Card(
            child: Padding(
                padding: const EdgeInsets.all(16),
                child: Semantics(
                    liveRegion: true,
                    child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Text('Bucket ${index + 1} of ${series.points.length}',
                              style: Theme.of(context).textTheme.titleMedium),
                          _field('Bucket start (UTC)',
                              series.points[index].at.toIso8601String()),
                          _field('Reported value',
                              series.metric.format(series.points[index].value)),
                          _field('Reported min / max',
                              '${series.metric.format(series.points[index].minimum)} / ${series.metric.format(series.points[index].maximum)}'),
                          _field(
                              'Coverage',
                              series.points[index].coverage == null
                                  ? 'Unavailable'
                                  : '${(series.points[index].coverage! * 100).toStringAsFixed(1)}%'),
                          _field('Observation count',
                              '${series.points[index].count ?? 'Unavailable'}'),
                        ])))),
      ],
    ]);
  }
}

class _Metric {
  const _Metric(this.label, this.unit, {this.bytes = false});
  final String label, unit;
  final bool bytes;
  bool get percent => unit == '%';
  double display(double value) => bytes ? value / (1024 * 1024 * 1024) : value;
  String format(double? value) => value == null
      ? 'Unavailable'
      : '${display(value).toStringAsFixed(bytes ? 2 : 1)} $unit';
}

const _metrics = {
  'cpu.utilization_percent': _Metric('CPU utilization', '%'),
  'memory.used_bytes': _Metric('Memory used', 'GiB', bytes: true),
  'memory.total_bytes': _Metric('Memory total', 'GiB', bytes: true),
  'memory.utilization_percent': _Metric('Memory utilization', '%'),
  'gpu.utilization_percent': _Metric('GPU utilization (first reported)', '%'),
  'gpu.memory_used_bytes':
      _Metric('GPU memory used (first reported)', 'GiB', bytes: true),
};

DateTime? _utc(Object? value) {
  if (value is! String ||
      !RegExp(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$')
          .hasMatch(value)) {
    return null;
  }
  final month = int.parse(value.substring(5, 7));
  final day = int.parse(value.substring(8, 10));
  if (month < 1 ||
      month > 12 ||
      day < 1 ||
      day > DateTime.utc(int.parse(value.substring(0, 4)), month + 1, 0).day ||
      int.parse(value.substring(11, 13)) > 23 ||
      int.parse(value.substring(14, 16)) > 59 ||
      int.parse(value.substring(17, 19)) > 59) {
    return null;
  }
  if (!value.endsWith('Z') &&
      (int.parse(value.substring(value.length - 5, value.length - 3)) > 23 ||
          int.parse(value.substring(value.length - 2)) > 59)) {
    return null;
  }
  return DateTime.tryParse(value)?.toUtc();
}

double? _number(Object? raw, _Metric metric) =>
    raw is num && raw.isFinite && raw >= 0 && (!metric.percent || raw <= 100)
        ? raw.toDouble()
        : null;

class _Bucket {
  _Bucket(this.at, this.value, this.minimum, this.maximum, this.coverage,
      this.count);
  final DateTime at;
  final double? value, minimum, maximum, coverage;
  final int? count;
}

class _Series {
  _Series(this.name, this.metric);
  final String name;
  final _Metric metric;
  final points = <_Bucket>[];
  int invalid = 0, returned = 0;
  bool unplaced = false;
  String? problem;
}

class _History {
  DateTime? from, to;
  int? step;
  String? problem;
  int unsupported = 0;
  final series = <_Series>[];
  static _History read(Json raw, String host) {
    final result = _History();
    result.from = _utc(raw['from']);
    result.to = _utc(raw['to']);
    final step = raw['step_seconds'];
    if (raw['schema_version'] != 1 || raw['host_id'] != host) {
      result.problem =
          'The response does not match this device or supported format.';
      return result;
    }
    if (result.from == null ||
        result.to == null ||
        !result.from!.isBefore(result.to!) ||
        step is! int ||
        step < 1 ||
        step > 9223372036 || // Go backend Duration's signed nanosecond limit.
        raw['series'] is! List ||
        (raw['series'] as List).length > 16) {
      result.problem =
          'The response has an invalid time range, step or series list.';
      return result;
    }
    result.step = step;
    final names = <String>{};
    for (final entry in raw['series'] as List) {
      if (entry is! Map ||
          entry['name'] is! String ||
          !names.add(entry['name'])) {
        result.problem = 'The response has invalid or duplicate series names.';
        return result;
      }
      final name = entry['name'] as String;
      final metric = _metrics[name];
      if (metric == null) {
        result.unsupported++;
        continue;
      }
      final series = _Series(name, metric);
      result.series.add(series);
      final points = entry['points'];
      if (points == null) continue; // Go nil points serialize as null.
      if (points is! List || points.length > 1000) {
        series.problem = 'A series must contain at most 1000 returned buckets.';
        continue;
      }
      series.returned = points.length;
      final timestamps = <DateTime>{};
      for (final rawPoint in points) {
        final point = rawPoint is Map ? rawPoint : const {};
        final at = _utc(point['at']);
        if (at == null ||
            at.isAfter(result.to!) ||
            !at.add(Duration(seconds: step)).isAfter(result.from!)) {
          series.invalid++;
          series.unplaced = true;
          continue;
        }
        if (!timestamps.add(at)) {
          series.problem =
              'Duplicate bucket timestamps cannot be plotted reliably.';
          break;
        }
        final value = _number(point['value'], metric);
        final minimum = _number(point['min'], metric);
        final maximum = _number(point['max'], metric);
        final coverage = point['coverage'];
        final count = point['count'];
        final valid = value != null &&
            minimum != null &&
            maximum != null &&
            minimum <= maximum &&
            value >= minimum - 1e-9 * math.max(1, value.abs()) &&
            value <= maximum + 1e-9 * math.max(1, value.abs()) &&
            coverage is num &&
            coverage.isFinite &&
            coverage >= 0 &&
            coverage <= 1 &&
            count is int &&
            count > 0;
        if (!valid) series.invalid++;
        series.points.add(_Bucket(
            at,
            valid ? value : null,
            valid ? minimum : null,
            valid ? maximum : null,
            valid ? coverage.toDouble() : null,
            valid ? count : null));
      }
      series.points.sort((a, b) => a.at.compareTo(b.at));
    }
    return result;
  }
}

double _chartMaximum(_Series series) {
  if (series.metric.percent) return 100;
  var maximum = 1.0;
  for (final point in series.points.where((p) => p.value != null)) {
    maximum = math.max(
        maximum,
        math.max(series.metric.display(point.maximum!),
            series.metric.display(point.value!)));
  }
  return maximum;
}

class _HistoryPainter extends CustomPainter {
  _HistoryPainter(this.series, this.from, this.to, this.step, this.selected,
      this.colors, this.focused);
  final _Series series;
  final DateTime from, to;
  final int step, selected;
  final ColorScheme colors;
  final bool focused;
  @override
  void paint(Canvas canvas, Size size) {
    final area = Rect.fromLTRB(8, 8, size.width - 8, size.height - 8);
    if (focused) {
      canvas.drawRect(
          Rect.fromLTWH(1, 1, size.width - 2, size.height - 2),
          Paint()
            ..color = colors.primary
            ..style = PaintingStyle.stroke
            ..strokeWidth = 2);
    }
    final maximum = _chartMaximum(series);
    Offset position(_Bucket point, double value) => Offset(
        area.left +
            area.width *
                point.at.difference(from).inMicroseconds /
                math.max(1, to.difference(from).inMicroseconds),
        area.bottom - area.height * series.metric.display(value) / maximum);
    final grid = Paint()..color = colors.outlineVariant;
    for (var i = 0; i <= 4; i++) {
      final y = area.top + area.height * i / 4;
      canvas.drawLine(Offset(area.left, y), Offset(area.right, y), grid);
    }
    final ink = Paint()
      ..color = colors.primary
      ..strokeWidth = 2;
    _Bucket? previous;
    for (final point in series.points) {
      if (point.value == null) {
        previous = null;
        continue;
      }
      final center = position(point, point.value!);
      if (!series.unplaced &&
          previous?.value != null &&
          previous!.coverage == 1 &&
          point.coverage == 1 &&
          point.at.difference(previous.at) == Duration(seconds: step)) {
        canvas.drawLine(position(previous, previous.value!), center, ink);
      }
      canvas.drawLine(position(point, point.minimum!),
          position(point, point.maximum!), ink);
      canvas.drawCircle(
          center,
          4,
          Paint()
            ..color = colors.primary
            ..strokeWidth = 2
            ..style = point.coverage == 1
                ? PaintingStyle.fill
                : PaintingStyle.stroke);
      previous = point;
    }
    if (selected >= 0 && selected < series.points.length) {
      final x = position(series.points[selected], 0).dx;
      final cursor = Paint()..color = colors.onSurfaceVariant;
      for (var y = area.top; y < area.bottom; y += 8) {
        canvas.drawLine(
            Offset(x, y), Offset(x, math.min(y + 4, area.bottom)), cursor);
      }
    }
  }

  @override
  bool shouldRepaint(covariant _HistoryPainter oldDelegate) => true;
}
