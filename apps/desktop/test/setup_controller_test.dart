import 'dart:async';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_client/odessy_client.dart';
import 'package:odessy_desktop/features/setup/controllers/setup_controller.dart';

class Backend implements SetupBackend {
  final requests = <SetupRequest>[];
  Completer<SetupReply>? pending;
  bool cancelled = false;
  @override
  Future<SetupReply> execute(SetupRequest request) async {
    requests.add(request);
    if (pending != null) return pending!.future;
    return SetupReply.fromJson({
      'id': request.id,
      'status': 'ok',
      'result': {
        'plan_id': 'review-id',
        'downloads': [],
        'readiness': 'needs_configuration',
        'selection': request.selection,
        'recovery': 'matches',
        'saved': true,
      }
    }, request.id);
  }

  @override
  Future<void> cancel() async {
    cancelled = true;
  }

  @override
  Future<void> close() async {}
}

void main() {
  test('finish automatically reviews verifies saves and connects', () async {
    final backend = Backend();
    var connected = false;
    final controller = SetupController(backend, onConnect: (_) async {
      connected = true;
    });
    controller.update({
      'role': 'combined',
      'model': {'provider': 'ollama', 'name': 'existing'}
    }, '/tmp/setup.json');
    await controller.finish();
    expect(backend.requests.map((r) => r.operation),
        ['review', 'verify', 'save', 'launch', 'activate']);
    expect(connected, isTrue);
    expect(controller.error, isEmpty);
    controller.dispose();
  });

  test('startup failure retries without another save', () async {
    final backend = Backend();
    var attempts = 0;
    final c = SetupController(backend, onConnect: (_) async {
      if (++attempts == 1) throw StateError('service unavailable');
    });
    c.update({
      'role': 'combined',
      'model': {'provider': 'ollama', 'name': 'existing'}
    }, '/tmp/retry.json');
    await c.finish();
    expect(c.error, contains('service unavailable'));
    await c.finish();
    expect(c.error, isEmpty);
    expect(attempts, 2);
    expect(backend.requests.where((r) => r.operation == 'save').length, 1);
    c.dispose();
  });

  test('cancelling startup stops the owned connector', () async {
    final ready = Completer<void>();
    final entered = Completer<void>();
    var cancelled = false;
    final c = SetupController(Backend(), onConnect: (_) async {
      entered.complete();
      await ready.future;
    }, onCancelConnect: () async {
      cancelled = true;
      ready.complete();
    });
    c.update({
      'role': 'combined',
      'model': {'provider': 'ollama', 'name': 'existing'}
    }, '/tmp/cancel.json');
    final completion = c.finish();
    await entered.future;
    await c.cancel();
    await completion;
    expect(cancelled, isTrue);
    expect(c.status, isNot('Connected to your configured model'));
    c.dispose();
  });

  test('restore uses saved model and never repeats a save', () async {
    final backend = RestoringBackend();
    Json? connected;
    final controller = SetupController(backend, onConnect: (spec) async {
      connected = spec;
    });
    await controller.restore();
    expect(backend.requests.map((r) => r.operation), ['resume']);
    expect(controller.saved, isTrue);
    expect(controller.activeSelection!['model']['name'], 'saved-q8');
    expect(connected!['config'], '/tmp/saved.env');
    expect(controller.error, isEmpty);
    controller.dispose();
  });

  test('review adopts backend revision path before saving changed model',
      () async {
    final backend = RevisionBackend();
    final c = SetupController(backend, onConnect: (_) async {});
    c.update({
      'role': 'combined',
      'model': {'provider': 'deepseek', 'name': 'deepseek-flash'}
    }, '/tmp/old.json');
    await c.finish();
    expect(c.output, '/tmp/new.json');
    expect(backend.requests.first.parameters['new_revision'], isTrue);
    expect(backend.requests.firstWhere((r) => r.operation == 'save').output,
        '/tmp/new.json');
    c.dispose();
  });

  test('discovery works before choosing a model name', () async {
    final backend = Backend();
    final controller = SetupController(backend);
    controller.update({
      'role': 'combined',
      'model': {'provider': 'ollama', 'name': ''}
    }, '/tmp/config.json');
    await controller.discover('http://127.0.0.1:11434');
    expect(backend.requests.single.selection, {'role': 'combined'});
    controller.dispose();
  });

  test('save requires review and edits invalidate it', () async {
    final backend = Backend();
    final controller = SetupController(backend);
    controller.update({'role': 'combined'}, '/tmp/selection.json');
    await controller.save();
    expect(backend.requests, isEmpty);
    await controller.review();
    expect(controller.canSave, isTrue);
    controller.update({'role': 'coordinator'}, '/tmp/selection.json');
    expect(controller.canSave, isFalse);
    await controller.save();
    expect(backend.requests.length, 1);
    controller.dispose();
  });
  test('cancel suppresses stale completion and recovery never repeats save',
      () async {
    final backend = Backend()..pending = Completer<SetupReply>();
    final controller = SetupController(backend);
    controller.update({'role': 'combined'}, '/tmp/recover.json');
    final operation = controller.review();
    await controller.cancel();
    final request = backend.requests.single;
    backend.pending!.complete(SetupReply.fromJson({
      'id': request.id,
      'status': 'ok',
      'result': {'plan_id': 'stale'}
    }, request.id));
    await operation;
    expect(controller.canSave, isFalse);
    expect(backend.cancelled, isTrue);
    backend.pending = null;
    await controller.recover();
    expect(backend.requests.last.operation, 'recover');
    expect(backend.requests.where((request) => request.operation == 'save'),
        isEmpty);
    controller.dispose();
  });
  test('cancelled save pins its recovery selection and destination', () async {
    final backend = Backend();
    final controller = SetupController(backend);
    controller.update({'role': 'combined'}, '/tmp/original.json');
    await controller.review();
    backend.pending = Completer<SetupReply>();
    final operation = controller.save();
    await controller.cancel();
    expect(controller.recoveryRequired, isTrue);
    controller.update({'role': 'execution'}, '/tmp/changed.json');
    expect(controller.output, '/tmp/original.json');
    final request = backend.requests.last;
    backend.pending!.complete(SetupReply.fromJson({
      'id': request.id,
      'status': 'ok',
      'result': {'saved': true}
    }, request.id));
    await operation;
    expect(controller.saved, isFalse);
    backend.pending = null;
    await controller.recover();
    expect(backend.requests.last.output, '/tmp/original.json');
    expect(backend.requests.last.selection['role'], 'combined');
    controller.dispose();
  });
}

class RestoringBackend extends Backend {
  @override
  Future<SetupReply> execute(SetupRequest request) async {
    if (request.operation != 'resume') return super.execute(request);
    requests.add(request);
    return SetupReply.fromJson({
      'id': request.id,
      'status': 'ok',
      'result': {
        'config': '/tmp/saved.env',
        'format': 'env',
        'python': 'python',
        'sha256': 'hash',
        'selection': {
          'role': 'combined',
          'model': {'provider': 'lfm', 'name': 'saved-q8'},
          'voice': {'enabled': false}
        }
      }
    }, request.id);
  }
}

class RevisionBackend extends Backend {
  @override
  Future<SetupReply> execute(SetupRequest request) async {
    if (request.operation != 'review') return super.execute(request);
    requests.add(request);
    return SetupReply.fromJson({
      'id': request.id,
      'status': 'ok',
      'result': {'plan_id': 'revision-id', 'output': '/tmp/new.json'}
    }, request.id);
  }
}
