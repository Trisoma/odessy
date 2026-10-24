import 'package:flutter/foundation.dart';

/// The named stages a packaged installation actually performs, in order.
///
/// Labels describe the real work; nothing is invented for a stage the engine
/// does not report.
enum SetupStageState { waiting, running, complete, failed, cancelled }

@immutable
class SetupStage {
  const SetupStage({
    required this.id,
    required this.label,
    this.state = SetupStageState.waiting,
    this.startedAt,
    this.finishedAt,
    this.detail,
  });

  final String id, label;
  final SetupStageState state;
  final DateTime? startedAt, finishedAt;
  final String? detail;

  Duration? get elapsed {
    final start = startedAt;
    if (start == null) return null;
    return (finishedAt ?? DateTime.now()).difference(start);
  }

  SetupStage start(DateTime at) => SetupStage(
      id: id, label: label, state: SetupStageState.running, startedAt: at);

  SetupStage finish(SetupStageState result, DateTime at, {String? detail}) =>
      SetupStage(
          id: id,
          label: label,
          state: result,
          startedAt: startedAt,
          finishedAt: at,
          detail: detail ?? this.detail);

  SetupStage withDetail(String value) => SetupStage(
      id: id,
      label: label,
      state: state,
      startedAt: startedAt,
      finishedAt: finishedAt,
      detail: value);
}

/// Engine journal stage -> user-facing stage.
const Map<String, String> journalStageIds = {
  'preparing': 'preparing',
  'checking': 'checking',
  'environment': 'runtime',
  'dependencies': 'dependencies',
  'components': 'components',
  'configuration': 'configuration',
  'activated': 'shortcuts',
};

/// Every packaged install begins with these, in this order.
const List<String> packagedStageOrder = [
  'preparing',
  'checking',
  'runtime',
  'dependencies',
  'components',
  'configuration',
  'shortcuts',
  'final',
];

const Map<String, String> packagedStageLabels = {
  'preparing': 'Preparing files',
  'checking': 'Checking package',
  'runtime': 'Creating runtime',
  'dependencies': 'Installing dependencies',
  'components': 'Installing selected components',
  'configuration': 'Saving configuration',
  'shortcuts': 'Creating shortcuts',
  'final': 'Final checks',
};

List<SetupStage> newPackagedStages() => [
      for (final id in packagedStageOrder)
        SetupStage(id: id, label: packagedStageLabels[id]!)
    ];

/// Stable stage list keyed by id, advancing without ever going backwards.
List<SetupStage> advanceStages(
  List<SetupStage> stages,
  String? engineStage,
  DateTime now, {
  bool busy = true,
  bool failed = false,
  bool cancelled = false,
  bool complete = false,
}) {
  final known = engineStage == null ? null : journalStageIds[engineStage];
  final int target;
  if (complete) {
    target = packagedStageOrder.length - 1;
  } else if (known != null) {
    target = packagedStageOrder.indexOf(known);
  } else {
    // No new engine stage: keep whichever stage is already running.
    final running =
        stages.indexWhere((s) => s.state == SetupStageState.running);
    target = running >= 0 ? running : 0;
  }
  final next = <SetupStage>[];
  var reached = false;
  for (var index = 0; index < stages.length; index++) {
    final stage = stages[index];
    if (index < target) {
      next.add(stage.state == SetupStageState.complete
          ? stage
          : stage.finish(SetupStageState.complete, now));
    } else if (index == target) {
      reached = true;
      if (stage.state == SetupStageState.complete) {
        next.add(stage);
      } else if (failed) {
        next.add(stage
            .start(stage.startedAt ?? now)
            .finish(SetupStageState.failed, now));
      } else if (cancelled) {
        next.add(stage
            .start(stage.startedAt ?? now)
            .finish(SetupStageState.cancelled, now));
      } else if (complete) {
        next.add(stage
            .start(stage.startedAt ?? now)
            .finish(SetupStageState.complete, now));
      } else {
        next.add(stage.start(stage.startedAt ?? now));
      }
    } else {
      next.add(stage);
    }
  }
  if (complete) {
    return [
      for (final stage in next)
        stage.state == SetupStageState.complete
            ? stage
            : stage.finish(SetupStageState.complete, now)
    ];
  }
  if (!reached && busy) {
    // Keep the last reachable stage running rather than showing everything idle.
    return next;
  }
  return next;
}
