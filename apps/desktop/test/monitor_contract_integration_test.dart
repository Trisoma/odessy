import 'dart:convert';
import 'dart:io';
import 'dart:async';
import 'package:odessy_client/odessy_client.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/features/monitoring/controllers/monitor_controller.dart';
import 'package:odessy_desktop/features/monitoring/presentation/metric_presentation.dart';

void main() {
  test('a failed refresh retains values as historical, not current', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    var fail = false;
    final subscription = server.listen((request) async {
      if (request.uri.path.endsWith('/events')) {
        request.response.headers.contentType = ContentType('text', 'event-stream');
        request.response.write(': keepalive\n\n');
        await request.response.flush();
        return;
      }
      if (fail) {
        request.response.statusCode = 503;
      } else if (request.uri.path.endsWith('/hosts')) {
        request.response.write(jsonEncode({'schema_version': 1, 'event_cursor': 'epoch:1', 'hosts': [{
          'host_id': 'host_local', 'trust': {'state': 'local'}, 'connectivity': {'state': 'online'},
          'monitoring_access': 'allowed'
        }]}));
      } else if (request.uri.path.endsWith('/metrics')) {
        request.response.write(jsonEncode({'schema_version': 1, 'sample': {
          'cpu': {'status': 'ok', 'utilization_percent': 25}
        }}));
      } else {
        request.response.write(jsonEncode({'schema_version': 1, 'series': []}));
      }
      await request.response.close();
    });
    final api = LocalApi(Uri.parse('http://127.0.0.1:${server.port}'), 'fixture');
    final controller = MonitorController();
    try {
      final ready = Completer<void>();
      controller.addListener(() {
        if (!controller.busy && !ready.isCompleted) ready.complete();
      });
      controller.bind(api);
      await ready.future.timeout(const Duration(seconds: 5));
      expect(controller.metrics, isNotNull);
      fail = true;
      await controller.refresh();
      expect(controller.metrics, isNotNull);
      expect(controller.historical, isTrue);
      expect(controller.message, contains('historical'));
    } finally {
      controller.dispose();
      api.close();
      await subscription.cancel();
      await server.close(force: true);
    }
  });
  test('monitor renders the shipped v1 device lists and metric field names', () {
    final schema = jsonDecode(File('../../contracts/monitoring/observation.schema.json').readAsStringSync()) as Map;
    final samples = <String, Map<String, dynamic>>{
      'disks': {'id': 'disk0', 'total_bytes': 4096, 'available_bytes': 1024},
      'network': {'id': 'eth0', 'rx_bytes_per_second': 2048, 'tx_bytes_per_second': 1024},
      'gpu': {'id': 'gpu0', 'utilization_percent': 12, 'power_watts': 8},
      'thermal': {'id': 'cpu', 'temperature_celsius': 42},
      'power': {'id': 'rail', 'watts': 5},
    };
    for (final entry in samples.entries) {
      final properties = schema['properties'][entry.key]['properties'] as Map;
      final fields = properties['devices']['items']['properties'] as Map;
      expect(fields.keys, containsAll(entry.value.keys));
      final summary = summariseGroup(entry.key, {'status': 'ok', 'devices': [entry.value]});
      expect(summary.devices, hasLength(1), reason: '${entry.key} must read devices from the real contract');
      expect(summary.devices.single.facts, isNotEmpty);
    }
    final memory = summariseGroup('memory', {'status': 'ok', 'swap_used_bytes': 1024});
    expect(memory.facts.single.display, '1.0 KiB');
  });

  test('invalid numeric metrics stay unknown rather than crashing formatting', () {
    for (final value in [double.nan, double.infinity, -1]) {
      final summary = summariseGroup('memory', {'status': 'ok', 'total_bytes': value});
      expect(summary.facts, isEmpty);
    }
  });
}
