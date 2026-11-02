import 'dart:io';

import 'package:odessy_desktop/app/setup/instance_guard.dart';

Future<void> main(List<String> args) async {
  final guard =
      await InstanceGuard.acquire('abc', directory: Directory(args[0]));
  stdout.writeln(guard == null ? 'busy' : 'locked');
  await guard?.release();
}
