import 'package:flutter/foundation.dart';
import 'package:odessy_client/odessy_client.dart';

/// One owner's section draft. Values hold credential references, never keys.
class SectionDraft {
  SectionDraft(Json value, this.revision)
      : baseline = Map.of(value),
        values = Map.of(value);
  Json baseline, values;
  String revision;
  bool get dirty => !mapEquals(baseline, values);
  void edit(Json next) => values = Map.of(next);
  void discard() => values = Map.of(baseline);
  void accept(Json next, String nextRevision) {
    baseline = Map.of(next);
    values = Map.of(next);
    revision = nextRevision;
  }
}
