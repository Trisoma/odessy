import 'package:flutter/material.dart';
import '../controllers/monitor_controller.dart';

class MonitorPage extends StatelessWidget {
  const MonitorPage({super.key, required this.controller});
  final MonitorController controller;
  @override
  Widget build(BuildContext context) => ListenableBuilder(
      listenable: controller,
      builder: (context, _) {
        final c = controller;
        final sample = c.metrics?['sample'] as Map?;
        final series = c.history?['series'] as List? ?? [];
        final points = series.isEmpty
            ? <dynamic>[]
            : (series.first['points'] as List? ?? []);
        return ListView(padding: const EdgeInsets.all(24), children: [
          Row(children: [
            Text('Host monitor',
                style: Theme.of(context).textTheme.headlineSmall),
            const Spacer(),
            IconButton(
                tooltip: 'Refresh monitoring',
                onPressed: c.busy ? null : c.refresh,
                icon: const Icon(Icons.refresh))
          ]),
          const Text(
              'Read-only telemetry · trust and connectivity are independent'),
          if (c.busy) const LinearProgressIndicator(),
          if (c.message.isNotEmpty)
            Padding(
                padding: const EdgeInsets.symmetric(vertical: 16),
                child: Text(c.message)),
          if (c.updated != null)
            Text('Last successful refresh: ${c.updated!.toLocal()}'),
          const SizedBox(height: 16),
          Wrap(spacing: 8, runSpacing: 8, children: [
            for (final h in c.hosts)
              ChoiceChip(
                  label: Text('${h.name} · ${h.connectivity}'),
                  selected: h.id == c.selected,
                  onSelected: c.busy ? null : (_) => c.select(h.id))
          ]),
          for (final h in c.hosts.where((h) => h.id == c.selected))
            Padding(
                padding: const EdgeInsets.symmetric(vertical: 16),
                child: SelectableText(
                    'Trust: ${h.trust}   •   Connection: ${h.connectivity}   •   Telemetry: ${h.access}\n${h.id}')),
          if (c.selected != null) ...[
            Text('Latest observation: ${sample?['observed_at'] ?? 'Unknown'}'),
            if ((c.metrics?['stale_groups'] as List? ?? []).isNotEmpty)
              Text(
                  'Stale groups: ${(c.metrics!['stale_groups'] as List).join(', ')}'),
            const SizedBox(height: 12),
            for (final group in [
              'cpu',
              'memory',
              'disks',
              'network',
              'gpu',
              'thermal',
              'power',
              'disk_io'
            ])
              _GroupCard(name: group, value: sample?[group]),
            const SizedBox(height: 20),
            Text('CPU history · past hour · 60-second buckets',
                style: Theme.of(context).textTheme.titleMedium),
            if (c.historyMessage.isNotEmpty) Text(c.historyMessage),
            if (points.isEmpty)
              const Padding(
                  padding: EdgeInsets.all(16),
                  child: Text(
                      'No CPU history yet. Warmup, missing samples and unavailable storage are not zero usage.'))
            else
              SizedBox(
                  height: 160,
                  child: CustomPaint(
                      painter: _HistoryPainter(
                          points, Theme.of(context).colorScheme.primary))),
            if (points.isNotEmpty)
              Text(
                  '${points.length} observed buckets · CPU utilization (%) · 0–100'),
          ],
        ]);
      });
}

class _GroupCard extends StatelessWidget {
  const _GroupCard({required this.name, this.value});
  final String name;
  final dynamic value;
  String describe(dynamic v) {
    if (v == null) return 'Unknown / unsupported';
    if (v is Map)
      return v.entries.map((e) => '${e.key}: ${describe(e.value)}').join(' · ');
    if (v is List)
      return v.isEmpty ? 'No reported devices' : v.map(describe).join('\n');
    if (v is num) return v is int ? '$v' : v.toStringAsFixed(2);
    return '$v';
  }

  @override
  Widget build(BuildContext context) => Card(
      child: Padding(
          padding: const EdgeInsets.all(14),
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(name.toUpperCase(),
                style: Theme.of(context).textTheme.labelLarge),
            const SizedBox(height: 6),
            SelectableText(describe(value)),
          ])));
}

class _HistoryPainter extends CustomPainter {
  _HistoryPainter(this.points, this.color);
  final List<dynamic> points;
  final Color color;
  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color
      ..strokeWidth = 2;
    final times = points
        .map((p) => DateTime.tryParse('${p['at']}')?.millisecondsSinceEpoch)
        .toList();
    final valid = times.whereType<int>().toList();
    if (valid.isEmpty) return;
    final start = valid.first,
        span = (valid.last - valid.first).clamp(1, 3600000);
    Offset? previous;
    int? previousTime;
    for (var i = 0; i < points.length; i++) {
      final value = points[i]['value'];
      final time = times[i];
      if (value is! num || time == null) {
        previous = null;
        continue;
      }
      final here = Offset((time - start) / span * size.width,
          size.height * (1 - value.clamp(0, 100) / 100));
      if (previous != null &&
          previousTime != null &&
          time - previousTime <= 90000) canvas.drawLine(previous, here, paint);
      canvas.drawCircle(here, 2, paint);
      previous = here;
      previousTime = time;
    }
  }

  @override
  bool shouldRepaint(covariant _HistoryPainter old) =>
      old.points != points || old.color != color;
}
