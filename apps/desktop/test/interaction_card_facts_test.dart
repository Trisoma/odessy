import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/features/conversation/presentation/interaction_card.dart';

void main() {
  test('approval facts lead with human-readable action, target and scope', () {
    final facts = approvalFacts({
      'tool_name': 'run_command',
      'execution_target': 'host_edge',
      'permission_scopes': ['workspace:read', 'process:exec'],
      'risk': 'high',
      'arguments': {'command': 'ls -la'},
    });
    expect(facts.map((f) => f.$1), [
      'Action',
      'Execution target',
      'Permission scope',
      'Risk',
      'Exact arguments',
    ]);
    expect(facts[0].$2, 'run_command');
    expect(facts[1].$2, 'host_edge');
    expect(facts[2].$2, 'workspace:read, process:exec');
    expect(facts[3].$2, 'high');
    expect(facts[4].$2, contains('command=ls -la'));
  });

  test('missing authorization facts stay explicitly unknown', () {
    final facts = approvalFacts({'tool': 'run_command'});
    expect(facts[0].$2, 'run_command');
    expect(facts[1].$2, 'not reported');
    expect(facts[2].$2, 'not reported');
    expect(facts[3].$2, 'not reported');
    expect(facts[4].$2, 'not reported');
  });

  test('empty scope and argument collections are not absent data', () {
    final facts = approvalFacts({
      'tool': 'read_file',
      'scopes': <Object>[],
      'arguments': <String, Object>{},
    });
    expect(facts[2].$2, 'none reported');
    expect(facts[4].$2, 'none reported');
  });
}
