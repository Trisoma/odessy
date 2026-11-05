import 'dart:math' as math;
import 'package:flutter/material.dart';
import '../../../app/session_controller.dart';
import '../controllers/usage_history_controller.dart';
import 'session_activity.dart';

/// Calendar buckets use UTC, matching the service's date-range filter.
List<Map> usageTimeline(List<Map> reports, int days, DateTime now) {
  if (days == 0) return reports;
  final today =
      DateTime.utc(now.toUtc().year, now.toUtc().month, now.toUtc().day);
  final byDay = <String, Map>{
    for (final row in reports)
      if (row['day'] is String) row['day'] as String: row
  };
  return List.generate(days, (index) {
    final date = today.subtract(Duration(days: days - 1 - index));
    final label = date.toIso8601String().substring(0, 10);
    return byDay[label] ?? {'day': label, 'recorded': false};
  });
}

/// History is supplied by the authenticated service, never by a widget.
class UsageHistoryDashboard extends StatefulWidget {
  const UsageHistoryDashboard({super.key, required this.session});
  final DesktopSession session;
  @override
  State<UsageHistoryDashboard> createState() => _UsageHistoryDashboardState();
}

class _UsageHistoryDashboardState extends State<UsageHistoryDashboard> {
  late final UsageHistoryController history;
  String metric = 'Tokens';
  String table = 'Requests';
  @override
  void initState() {
    super.initState();
    history = UsageHistoryController(widget.session);
  }

  @override
  void dispose() {
    history.dispose();
    super.dispose();
  }

  static String _number(Object? value) {
    if (value is! num || !value.isFinite || value < 0) return 'Not reported';
    final raw = value.toInt().toString();
    return raw.replaceAllMapped(RegExp(r'\B(?=(\d{3})+(?!\d))'), (_) => ',');
  }

  static String _cost(Object? value, Object? currency) {
    if (value is! num || !value.isFinite || value < 0) return 'Not reported';
    final unit = currency is String && currency.isNotEmpty
        ? currency
        : 'currency unknown';
    return '${value.toStringAsFixed(4)} $unit';
  }

  static String _label(Object? value) =>
      value is String && value.trim().isNotEmpty ? value : 'Not reported';
  static List<Map> _maps(Object? value) =>
      value is List ? value.whereType<Map>().toList() : [];

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
        animation: Listenable.merge([history, widget.session]),
        builder: (context, _) {
          final theme = Theme.of(context);
          final data = history.data;
          final summary =
              data?['summary'] is Map ? data!['summary'] as Map : const {};
          final options = _maps(data?['options']);
          final providers = options
              .map((e) => e['provider'])
              .whereType<String>()
              .toSet()
              .toList()
            ..sort();
          if (history.provider != null &&
              !providers.contains(history.provider)) {
            providers.add(history.provider!);
            providers.sort();
          }
          final models = options
              .where((e) =>
                  history.provider == null || e['provider'] == history.provider)
              .map((e) => e['model'])
              .whereType<String>()
              .toSet()
              .toList()
            ..sort();
          if (history.model != null && !models.contains(history.model)) {
            models.add(history.model!);
            models.sort();
          }
          final daily = usageTimeline(
              _maps(data?['daily']), history.days, DateTime.now().toUtc());
          final rows = _maps(data?['requests']);
          final groups = _maps(data?['groups']);
          final providerGroups = _maps(data?['providers']);
          final total = data?['total'] is int ? data!['total'] as int : 0;
          final input = summary['prompt'], output = summary['completion'];
          final tokens = input is int && output is int ? input + output : null;
          return SingleChildScrollView(
            key: const Key('usage-history-dashboard'),
            padding: const EdgeInsets.symmetric(horizontal: 28, vertical: 24),
            child: Center(
                child: ConstrainedBox(
                    constraints: const BoxConstraints(maxWidth: 1360),
                    child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Wrap(
                              alignment: WrapAlignment.spaceBetween,
                              crossAxisAlignment: WrapCrossAlignment.center,
                              spacing: 16,
                              runSpacing: 12,
                              children: [
                                Column(
                                    crossAxisAlignment:
                                        CrossAxisAlignment.start,
                                    children: [
                                      Text('Usage overview',
                                          style: theme.textTheme.headlineMedium
                                              ?.copyWith(
                                                  fontWeight: FontWeight.w700)),
                                      const SizedBox(height: 4),
                                      Text(
                                          'Understand your model activity at a glance',
                                          style: theme.textTheme.bodyMedium
                                              ?.copyWith(
                                                  color: theme.colorScheme
                                                      .onSurfaceVariant)),
                                    ]),
                                OutlinedButton.icon(
                                    onPressed: history.loading
                                        ? null
                                        : history.refresh,
                                    icon: const Icon(Icons.refresh, size: 18),
                                    label: const Text('Refresh')),
                              ]),
                          const SizedBox(height: 16),
                          if (widget.session.api.isDemo)
                            _notice(context, Icons.science_outlined,
                                'Demo data · Sample requests only. No model or tool was run.')
                          else
                            _notice(context, Icons.lock_outline,
                                'Stored locally · Provider-reported calls only · Estimated costs are not a bill.'),
                          if (!widget.session.connected)
                            _notice(context, Icons.cloud_off_outlined,
                                'Service disconnected. Reconnect to view usage history.'),
                          if (history.error != null)
                            _notice(
                                context, Icons.error_outline, history.error!),
                          if (data?['recording_error'] == true)
                            _notice(context, Icons.warning_amber_outlined,
                                'Some usage could not be saved during this service run. History may be incomplete.'),
                          const SizedBox(height: 16),
                          Wrap(
                              spacing: 10,
                              runSpacing: 10,
                              crossAxisAlignment: WrapCrossAlignment.center,
                              children: [
                                _filter<int>(
                                    context,
                                    'Date range',
                                    history.days,
                                    const {
                                      7: 'Last 7 days',
                                      30: 'Last 30 days',
                                      90: 'Last 90 days',
                                      0: 'All time'
                                    },
                                    (value) =>
                                        history.setFilters(range: value)),
                                _filter<String>(
                                    context,
                                    'Provider',
                                    history.provider ?? '',
                                    {
                                      '': 'All providers',
                                      for (final item in providers) item: item
                                    },
                                    (value) => history.setFilters(
                                        selectedProvider:
                                            value.isEmpty ? null : value,
                                        clearProvider: value.isEmpty,
                                        clearModel: true)),
                                _filter<String>(
                                    context,
                                    'Model',
                                    history.model ?? '',
                                    {
                                      '': 'All models',
                                      for (final item in models) item: item
                                    },
                                    (value) => history.setFilters(
                                        selectedModel:
                                            value.isEmpty ? null : value,
                                        clearModel: value.isEmpty)),
                              ]),
                          if (history.loading)
                            const Padding(
                                padding: EdgeInsets.only(top: 16),
                                child: LinearProgressIndicator()),
                          const SizedBox(height: 18),
                          if (data == null &&
                              !history.loading &&
                              history.error == null &&
                              widget.session.connected)
                            const Text('No usage history is available yet.')
                          else if (data != null) ...[
                            LayoutBuilder(builder: (context, constraints) {
                              final columns = constraints.maxWidth > 980
                                  ? 4
                                  : constraints.maxWidth > 550
                                      ? 2
                                      : 1;
                              final width =
                                  (constraints.maxWidth - (columns - 1) * 12) /
                                      columns;
                              return Wrap(
                                  spacing: 12,
                                  runSpacing: 12,
                                  children: [
                                    _summary(
                                        context,
                                        width,
                                        'Estimated cost',
                                        _cost(summary['cost'],
                                            summary['currency']),
                                        Icons.account_balance_wallet_outlined,
                                        summary['cost'] == null &&
                                                (summary['requests'] as int? ??
                                                        0) >
                                                    0
                                            ? 'Price, tokens or currency unavailable'
                                            : 'Configured prices'),
                                    _summary(
                                        context,
                                        width,
                                        'Reported requests',
                                        _number(summary['requests']),
                                        Icons.bolt_outlined,
                                        'With provider usage'),
                                    _summary(
                                        context,
                                        width,
                                        'Total tokens',
                                        _number(tokens),
                                        Icons.toll_outlined,
                                        'Input + output'),
                                    _summary(
                                        context,
                                        width,
                                        'Cached input',
                                        _number(summary['cached']),
                                        Icons.cached,
                                        'Provider reported'),
                                  ]);
                            }),
                            const SizedBox(height: 18),
                            _panel(context,
                                child: Column(
                                    crossAxisAlignment:
                                        CrossAxisAlignment.stretch,
                                    children: [
                                      Wrap(
                                          alignment: WrapAlignment.spaceBetween,
                                          crossAxisAlignment:
                                              WrapCrossAlignment.center,
                                          spacing: 10,
                                          runSpacing: 10,
                                          children: [
                                            Column(
                                                crossAxisAlignment:
                                                    CrossAxisAlignment.start,
                                                children: [
                                                  Text('Usage trend',
                                                      style: theme
                                                          .textTheme.titleLarge
                                                          ?.copyWith(
                                                              fontWeight:
                                                                  FontWeight
                                                                      .w700)),
                                                  const SizedBox(height: 4),
                                                  Text('Calendar days · UTC',
                                                      style: theme
                                                          .textTheme.bodySmall),
                                                ]),
                                            SegmentedButton<String>(
                                                segments: const [
                                                  ButtonSegment(
                                                      value: 'Tokens',
                                                      label: Text('Tokens')),
                                                  ButtonSegment(
                                                      value: 'Requests',
                                                      label: Text('Requests')),
                                                  ButtonSegment(
                                                      value: 'Cost',
                                                      label: Text('Cost'))
                                                ],
                                                selected: {
                                                  metric
                                                },
                                                showSelectedIcon: false,
                                                onSelectionChanged: (value) =>
                                                    setState(() =>
                                                        metric = value.first)),
                                          ]),
                                      const SizedBox(height: 22),
                                      _chart(context, daily),
                                    ])),
                            const SizedBox(height: 22),
                            Wrap(
                                alignment: WrapAlignment.spaceBetween,
                                crossAxisAlignment: WrapCrossAlignment.center,
                                runSpacing: 10,
                                spacing: 12,
                                children: [
                                  Text('Request history',
                                      style: theme.textTheme.titleLarge
                                          ?.copyWith(
                                              fontWeight: FontWeight.w700)),
                                  SegmentedButton<String>(
                                      segments: const [
                                        ButtonSegment(
                                            value: 'Requests',
                                            label: Text('Requests')),
                                        ButtonSegment(
                                            value: 'Providers',
                                            label: Text('Providers')),
                                        ButtonSegment(
                                            value: 'Models',
                                            label: Text('Models'))
                                      ],
                                      selected: {
                                        table
                                      },
                                      showSelectedIcon: false,
                                      onSelectionChanged: (value) =>
                                          setState(() => table = value.first)),
                                ]),
                            const SizedBox(height: 12),
                            if (table != 'Requests')
                              Padding(
                                padding: const EdgeInsets.only(bottom: 8),
                                child: Text(
                                    'Showing up to the top 100 ${table.toLowerCase()} by request count.',
                                    style: theme.textTheme.bodySmall),
                              ),
                            if (total == 0)
                              _panel(context,
                                  child: const Padding(
                                      padding:
                                          EdgeInsets.symmetric(vertical: 22),
                                      child: Text(
                                          'No reported usage for these filters. New provider reports will appear here.')))
                            else
                              _panel(context,
                                  child: table == 'Requests'
                                      ? _requests(context, rows)
                                      : _groups(
                                          context,
                                          table == 'Providers'
                                              ? providerGroups
                                              : groups,
                                          showModel: table == 'Models')),
                            if (table == 'Requests' && total > 0)
                              Padding(
                                  padding: const EdgeInsets.only(top: 8),
                                  child: Row(
                                      mainAxisAlignment: MainAxisAlignment.end,
                                      children: [
                                        Text(
                                            '${history.offset + 1}–${math.min(history.offset + rows.length, total)} of $total'),
                                        IconButton(
                                            tooltip: 'Previous page',
                                            onPressed: history.offset == 0
                                                ? null
                                                : () => history.setOffset(
                                                    history.offset - 20),
                                            icon:
                                                const Icon(Icons.chevron_left)),
                                        IconButton(
                                            tooltip: 'Next page',
                                            onPressed:
                                                history.offset + rows.length >=
                                                        total
                                                    ? null
                                                    : () => history.setOffset(
                                                        history.offset + 20),
                                            icon: const Icon(
                                                Icons.chevron_right)),
                                      ])),
                          ],
                          const SizedBox(height: 16),
                          Text(
                              'History begins when recording is enabled. Local models, failed calls without a usage report, and coding CLI usage may be absent. Missing values remain unknown.',
                              style: theme.textTheme.bodySmall?.copyWith(
                                  color: theme.colorScheme.onSurfaceVariant)),
                          const SizedBox(height: 12),
                          SessionActivity(
                              events:
                                  widget.session.session?.events ?? const [],
                              showUsage: history.error != null ||
                                  !widget.session.connected),
                        ]))),
          );
        },
      );

  Widget _notice(BuildContext context, IconData icon, String text) => Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Icon(icon, size: 16, color: Theme.of(context).colorScheme.primary),
        const SizedBox(width: 8),
        Expanded(
            child: Text(text, style: Theme.of(context).textTheme.bodySmall)),
      ]));

  Widget _filter<T>(BuildContext context, String label, T value,
          Map<T, String> choices, ValueChanged<T> onChanged) =>
      SizedBox(
          width: 220,
          child: DropdownButtonFormField<T>(
              initialValue:
                  choices.containsKey(value) ? value : choices.keys.first,
              isExpanded: true,
              decoration: InputDecoration(
                  labelText: label,
                  isDense: true,
                  border: const OutlineInputBorder()),
              items: [
                for (final entry in choices.entries)
                  DropdownMenuItem(
                      value: entry.key,
                      child: Text(entry.value, overflow: TextOverflow.ellipsis))
              ],
              onChanged: (next) {
                if (next != null) onChanged(next);
              }));

  Widget _panel(BuildContext context, {required Widget child}) => Card(
      margin: EdgeInsets.zero,
      child: Padding(padding: const EdgeInsets.all(20), child: child));

  Widget _summary(BuildContext context, double width, String title,
          String value, IconData icon, String detail) =>
      SizedBox(
          width: width,
          child: _panel(context,
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(children: [
                      Expanded(
                          child: Text(title,
                              style: Theme.of(context).textTheme.bodySmall)),
                      Icon(icon,
                          size: 18,
                          color: Theme.of(context).colorScheme.primary)
                    ]),
                    const SizedBox(height: 12),
                    Text(value,
                        style: Theme.of(context)
                            .textTheme
                            .headlineSmall
                            ?.copyWith(fontWeight: FontWeight.w700)),
                    const SizedBox(height: 5),
                    Text(detail,
                        style: Theme.of(context).textTheme.bodySmall?.copyWith(
                            color: Theme.of(context)
                                .colorScheme
                                .onSurfaceVariant)),
                  ])));

  Widget _chart(BuildContext context, List<Map> days) {
    if (days.isEmpty || days.every((day) => day['recorded'] == false)) {
      return const SizedBox(
          height: 150,
          child: Center(child: Text('No usage recorded for this range.')));
    }
    final currencies = days
        .where((day) => day['cost'] != null)
        .map((day) => day['currency'])
        .whereType<String>()
        .toSet();
    if (metric == 'Cost' && currencies.length > 1) {
      return const SizedBox(
          height: 150,
          child: Center(
              child: Text(
                  'Cost trend is unavailable across multiple currencies.')));
    }
    num? value(Map row) {
      if (metric == 'Requests') return row['requests'] as num?;
      if (metric == 'Cost') return row['cost'] as num?;
      final a = row['prompt'], b = row['completion'];
      return a is num && b is num ? a + b : null;
    }

    final values = days.map(value).toList();
    final valid =
        values.whereType<num>().where((v) => v.isFinite && v >= 0).toList();
    if (valid.isEmpty) {
      return const SizedBox(
          height: 150,
          child: Center(child: Text('This metric was not reported.')));
    }
    final maxValue = valid.fold<num>(0, math.max);
    final color = Theme.of(context).colorScheme.primary;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      SizedBox(
          height: 150,
          child: Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
            for (var i = 0; i < days.length; i++)
              Expanded(
                  child: Tooltip(
                      message:
                          '${_label(days[i]['day'])}: ${days[i]['recorded'] == false ? 'No recorded requests' : values[i] == null ? 'Not reported' : metric == 'Cost' ? _cost(values[i], days[i]['currency']) : _number(values[i])}',
                      child: Semantics(
                          label:
                              '${_label(days[i]['day'])}, ${days[i]['recorded'] == false ? 'no recorded requests' : '$metric ${values[i] ?? 'not reported'}'}',
                          child: Padding(
                              padding:
                                  const EdgeInsets.symmetric(horizontal: 2),
                              child: Align(
                                  alignment: Alignment.bottomCenter,
                                  child: Container(
                                      constraints:
                                          const BoxConstraints(maxWidth: 32),
                                      height: values[i] == null || maxValue == 0
                                          ? 2
                                          : math
                                              .max(3,
                                                  145 * values[i]! / maxValue)
                                              .toDouble(),
                                      decoration: BoxDecoration(
                                          color: values[i] == null
                                              ? color.withValues(alpha: .15)
                                              : color,
                                          borderRadius: const BorderRadius
                                              .vertical(
                                              top: Radius.circular(4))))))))),
          ])),
      const SizedBox(height: 8),
      Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
        Text(_label(days.first['day']),
            style: Theme.of(context).textTheme.bodySmall),
        Text(_label(days.last['day']),
            style: Theme.of(context).textTheme.bodySmall),
      ]),
    ]);
  }

  Widget _requests(BuildContext context, List<Map> rows) =>
      SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          child: DataTable(columnSpacing: 28, columns: const [
            DataColumn(label: Text('Time')),
            DataColumn(label: Text('Provider')),
            DataColumn(label: Text('Model')),
            DataColumn(label: Text('Input')),
            DataColumn(label: Text('Output')),
            DataColumn(label: Text('Cached')),
            DataColumn(label: Text('Est. cost'))
          ], rows: [
            for (final row in rows)
              DataRow(
                  onSelectChanged: (_) => _showRequest(context, row),
                  cells: [
                    DataCell(Text(_formatTime(row['recorded_at']))),
                    DataCell(Text(_label(row['provider']))),
                    DataCell(Text(_label(row['model']))),
                    DataCell(Text(_number(row['prompt']))),
                    DataCell(Text(_number(row['completion']))),
                    DataCell(Text(_number(row['cached']))),
                    DataCell(Text(_cost(row['cost'], row['currency']))),
                  ])
          ]));

  Widget _groups(BuildContext context, List<Map> groups,
          {required bool showModel}) =>
      SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          child: DataTable(columnSpacing: 28, columns: [
            const DataColumn(label: Text('Provider')),
            if (showModel) const DataColumn(label: Text('Model')),
            const DataColumn(label: Text('Requests')),
            const DataColumn(label: Text('Input')),
            const DataColumn(label: Text('Output')),
            const DataColumn(label: Text('Est. cost'))
          ], rows: [
            for (final row in groups)
              DataRow(cells: [
                DataCell(Text(_label(row['provider']))),
                if (showModel) DataCell(Text(_label(row['model']))),
                DataCell(Text(_number(row['requests']))),
                DataCell(Text(_number(row['prompt']))),
                DataCell(Text(_number(row['completion']))),
                DataCell(Text(_cost(row['cost'], row['currency']))),
              ])
          ]));

  void _showRequest(BuildContext context, Map row) {
    showDialog<void>(
        context: context,
        builder: (context) => AlertDialog(
              title: const Text('Usage report'),
              content: SingleChildScrollView(
                  child: Column(
                      mainAxisSize: MainAxisSize.min,
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                    Text('Time: ${_formatTime(row['recorded_at'])}'),
                    Text('Provider: ${_label(row['provider'])}'),
                    Text('Model: ${_label(row['model'])}'),
                    Text('Profile: ${_label(row['participant'])}'),
                    const SizedBox(height: 12),
                    Text('Input tokens: ${_number(row['prompt'])}'),
                    Text('Cached input tokens: ${_number(row['cached'])}'),
                    Text('Output tokens: ${_number(row['completion'])}'),
                    Text(
                        'Estimated cost: ${_cost(row['cost'], row['currency'])}'),
                    const SizedBox(height: 12),
                    const Text(
                        'Only provider-reported usage is shown. Estimated cost is not a bill.'),
                  ])),
              actions: [
                TextButton(
                    onPressed: () => Navigator.pop(context),
                    child: const Text('Close'))
              ],
            ));
  }

  String _formatTime(Object? raw) {
    final parsed = raw is String ? DateTime.tryParse(raw)?.toLocal() : null;
    if (parsed == null) return 'Not reported';
    String two(int n) => n.toString().padLeft(2, '0');
    return '${parsed.year}-${two(parsed.month)}-${two(parsed.day)} ${two(parsed.hour)}:${two(parsed.minute)}';
  }
}
