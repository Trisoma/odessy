/// Reusable typed draft with explicit provenance and apply timing.
class SettingField<T> {
  SettingField(
      {required this.source,
      required this.applyTiming,
      required this.parse,
      required this.validate});
  final String source;
  final String applyTiming;
  final T? Function(String) parse;
  final String? Function(T) validate;
  String text = '';
  String? _baseline;
  T? value;
  String? error;
  bool get dirty => _baseline != null && text != _baseline;
  void load(T next) {
    _baseline = '$next';
    edit('$next');
  }

  void edit(String next) {
    text = next;
    value = parse(next);
    error = value == null ? 'Enter a valid value.' : validate(value as T);
  }
}
