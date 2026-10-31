import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/features/setup/controllers/package_setup_controller.dart';
import 'setup_controller_test.dart' show Backend;

void main() {
  test('packaged install requires reviewed exact inputs', () async {
    final backend = Backend();
    final c = PackageSetupController(backend);
    c.context = {'payload_root': 'C:/payload'};
    c.edit(destination: 'C:/target');
    await c.run('package-install');
    expect(backend.requests, isEmpty);
    await c.run('package-review');
    expect(c.review['plan_id'], 'review-id');
    c.edit(destination: 'C:/changed');
    expect(c.review, isEmpty);
    await c.run('package-review');
    await c.run('package-install');
    final install = backend.requests
        .singleWhere((r) => r.parameters['action'] == 'package-install');
    expect(install.parameters['confirm'], 'review-id');
    expect(install.parameters['payload'], 'C:/payload');
    expect(install.parameters['root'], 'C:/changed');
  });
}
