import 'package:file_selector/file_selector.dart';

/// Native folder/file selection behind a small injectable interface.
///
/// Presentation never touches the filesystem; tests inject a fake. A cancelled
/// dialog returns null and changes nothing.
abstract interface class NativePicker {
  Future<String?> pickDirectory({String? initial, String? title});
  Future<String?> pickFile({String? initial, List<String> extensions});
  Future<String?> pickSaveLocation({
    required String suggestedName,
    List<String> extensions,
  });
}

class FileSelectorPicker implements NativePicker {
  const FileSelectorPicker();

  @override
  Future<String?> pickDirectory({String? initial, String? title}) =>
      getDirectoryPath(initialDirectory: initial, confirmButtonText: 'Use this folder');

  @override
  Future<String?> pickFile({String? initial, List<String> extensions = const []}) async {
    final group = extensions.isEmpty
        ? null
        : XTypeGroup(label: 'Files', extensions: extensions);
    final file = await openFile(
        initialDirectory: initial,
        acceptedTypeGroups: group == null ? const [] : [group]);
    return file?.path;
  }

  @override
  Future<String?> pickSaveLocation({
    required String suggestedName,
    List<String> extensions = const [],
  }) async {
    final group = extensions.isEmpty
        ? null
        : XTypeGroup(label: 'Files', extensions: extensions);
    final location = await getSaveLocation(
        suggestedName: suggestedName,
        acceptedTypeGroups: group == null ? const [] : [group]);
    return location?.path;
  }
}

/// Used where a picker is not available (headless tests, unsupported platform).
class UnavailablePicker implements NativePicker {
  const UnavailablePicker();
  @override
  Future<String?> pickDirectory({String? initial, String? title}) async => null;
  @override
  Future<String?> pickFile({String? initial, List<String> extensions = const []}) async =>
      null;
  @override
  Future<String?> pickSaveLocation({
    required String suggestedName,
    List<String> extensions = const [],
  }) async =>
      null;
}
