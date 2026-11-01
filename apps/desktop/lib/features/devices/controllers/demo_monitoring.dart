import 'package:odessy_client/odessy_client.dart';

/// Explicit DEMO source only. It cannot make requests or replace a live failure.
class DemoMonitoring implements MonitoringClient {
  DemoMonitoring(this.clock);
  final DateTime Function() clock;
  bool closed = false;
  @override
  Uri get base => Uri(scheme: 'demo', path: 'monitoring');
  void check() {
    if (closed) throw StateError('Demo monitoring is closed.');
  }

  List<MonitoringHost> get hosts => [
        for (final item in [
          ('host_demo_local', 'This computer'),
          ('host_demo_jetson', 'Jetson'),
          ('host_demo_laptop', 'Laptop host')
        ])
          MonitoringHost.fromJson({
            'host_id': item.$1,
            'display_name': item.$2,
            'trust': {'state': 'paired'},
            'connectivity': {'state': 'online'},
            'monitoring_access': 'allowed'
          })
      ];
  @override
  Future<MonitoringSnapshot> listHosts({String? cursor, int limit = 20}) async {
    check();
    return MonitoringSnapshot(hosts, 'demo:1', null);
  }

  @override
  Future<MonitoringHost> getHost(String id) async {
    check();
    return hosts.firstWhere((h) => h.id == id);
  }

  @override
  Future<Json> getOverview() async {
    check();
    return {'schema_version': 1, 'history_degrade': ''};
  }

  @override
  Future<Json> getMetrics(String id) async {
    check();
    return {
      'schema_version': 1,
      'host_id': id,
      'stale_groups': [],
      'sample': {
        'observed_at': clock().toUtc().toIso8601String(),
        'cpu': {'status': 'ok', 'utilization_percent': 17.5},
        'memory': {
          'status': 'ok',
          'used_bytes': 8589934592,
          'total_bytes': 17179869184
        },
        'gpu': {'status': 'unsupported', 'devices': []}
      },
      'services': []
    };
  }

  @override
  Future<Json> getHistory(String id,
      {required DateTime from,
      required DateTime to,
      int step = 60,
      String series = 'cpu.utilization_percent'}) async {
    check();
    final bucket = DateTime.fromMillisecondsSinceEpoch(
        from.millisecondsSinceEpoch ~/ (step * 1000) * (step * 1000),
        isUtc: true);
    return {
      'schema_version': 1,
      'host_id': id,
      'from': from.toUtc().toIso8601String(),
      'to': to.toUtc().toIso8601String(),
      'step_seconds': step,
      'series': [
        for (final name in series.split(','))
          {
            'name': name,
            'points': name.startsWith('gpu.')
                ? null
                : [
                    for (var i = 0; i < 8; i++)
                      {
                        'at': bucket
                            .add(Duration(seconds: step * i))
                            .toIso8601String(),
                        'value': name == 'memory.used_bytes'
                            ? 8589934592
                            : name == 'memory.total_bytes'
                                ? 17179869184
                                : 20.0 + i * 4,
                        'min': name == 'memory.used_bytes'
                            ? 8589934592
                            : name == 'memory.total_bytes'
                                ? 17179869184
                                : 20.0 + i * 4,
                        'max': name == 'memory.used_bytes'
                            ? 8589934592
                            : name == 'memory.total_bytes'
                                ? 17179869184
                                : 20.0 + i * 4,
                        'count': 6,
                        'coverage': i == 3 ? 0.5 : 1.0
                      }
                  ]
          }
      ]
    };
  }

  @override
  Stream<MonitoringEvent> watchEvents({String? after}) => const Stream.empty();
  @override
  void close() {
    closed = true;
  }
}
