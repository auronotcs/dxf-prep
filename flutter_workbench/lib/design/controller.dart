import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';

typedef Json = Map<String, dynamic>;
double number(dynamic n) => (n as num?)?.toDouble() ?? 0;
Offset point(dynamic p) => Offset(number(p['x']), number(p['y']));
Rect? bounds(dynamic b) => b is List && b.length == 4
    ? Rect.fromLTRB(number(b[0]), number(b[1]), number(b[2]), number(b[3]))
    : null;

class DrawingClipboard extends ChangeNotifier {
  DesignController? owner;
  Json? data;
  int get count => [
    'entities',
    'dimensions',
    'guides',
  ].fold(0, (sum, key) => sum + (data?[key] as List? ?? []).length);
  void set(DesignController source, Json value) {
    owner = source;
    data = value;
    notifyListeners();
  }
}

class DesignController extends ChangeNotifier {
  DesignController({this.recoveryEnabled = false, this.recoveryDirectory});
  final bool recoveryEnabled;
  final String? recoveryDirectory;
  DrawingClipboard? drawingClipboard;
  bool get hasClipboard => drawingClipboard?.data != null
      ? drawingClipboard!.count > 0
      : number(state['clipboardCount']) > 0;

  Future<bool> copyDrawing({bool cut = false, Json params = const {}}) async {
    if (!await run(cut ? 'design.cut' : 'design.copy', params)) return false;
    if (state['annotationClipboard'] == true && drawingClipboard != null) {
      try {
        final result = await request('design.clipboard_export');
        if (result['ok'] != true) throw StateError(result['error'].toString());
        drawingClipboard!.set(this, result['clipboard'] as Json);
      } catch (e) {
        message = 'Copied within this drawing; sharing across tabs failed: $e';
        notifyListeners();
      }
    }
    return true;
  }

  Future<bool> pasteDrawing([Json params = const {}]) => run('design.paste', {
    ...params,
    if (drawingClipboard?.data != null && drawingClipboard?.owner != this)
      'clipboard': drawingClipboard!.data,
  });
  Process? process;
  final pending = <int, Completer<Json>>{};
  int sequence = 0;
  Json state = {};
  Json preferences = {};
  String message = 'Starting the Python Design engine…';
  bool busy = true;
  bool ready = false;
  bool stopped = false;
  String diagnostics = '';
  Directory? root;
  List<Json> get entities => (state['entities'] as List? ?? []).cast<Json>();
  List<Json> get layers => (state['layers'] as List? ?? []).cast<Json>();
  List<Json> get plates => (state['plates'] as List? ?? []).cast<Json>();
  Set<String> get selection => Set<String>.from(state['selected'] ?? []);
  bool get readOnly => state['readOnly'] == true;
  String get units => state['units'] ?? 'in';
  bool get dirty => state['dirty'] == true;

  Future<void> start() async {
    try {
      final candidates = <Directory>[
        Directory.current,
        Directory('${Directory.current.path}/flutter_workbench'),
      ];
      var dir = File(Platform.resolvedExecutable).parent;
      for (var i = 0; i < 8; i++) {
        candidates.add(dir);
        dir = dir.parent;
      }
      root = candidates
          .where((d) => File('${d.path}/backend/design_bridge.py').existsSync())
          .firstOrNull;
      if (root == null) {
        throw StateError(
          'Cannot find backend/design_bridge.py beside this project. Use the Windows launcher.',
        );
      }
      final python =
          '${Platform.environment['USERPROFILE']}/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe';
      process = await Process.start(
        python,
        ['-u', '${root!.path}/backend/design_bridge.py'],
        workingDirectory: root!.path,
        environment: {
          'PYTHONDONTWRITEBYTECODE': '1',
          'PYTHONIOENCODING': 'utf-8',
          'WORKBENCH_RECOVERY_ENABLED': recoveryEnabled ? '1' : '0',
          'WORKBENCH_RECOVERY_DIRECTORY': recoveryDirectory ?? '',
        },
      );
      process!.stdout
          .transform(utf8.decoder)
          .transform(const LineSplitter())
          .listen((line) {
            try {
              final response = jsonDecode(line) as Json;
              pending.remove(response['id'])?.complete(response);
            } catch (e) {
              diagnostics = '$e\n$line';
            }
          }, onError: (Object error) => fail(error.toString()));
      process!.stderr.transform(utf8.decoder).listen((s) {
        diagnostics = (diagnostics + s);
        if (diagnostics.length > 4000) {
          diagnostics = diagnostics.substring(diagnostics.length - 4000);
        }
      });
      unawaited(
        process!.exitCode.then((code) {
          if (!stopped) fail('Design engine stopped ($code). $diagnostics');
        }),
      );
      final response = await request('hello');
      if (response['protocol'] != 1) {
        throw StateError('Unsupported Design bridge version.');
      }
      state = response['state'] as Json;
      preferences = (response['preferences'] as Json?) ?? {};
      ready = true;
      busy = false;
      message = 'Design ready · ${entities.length} objects';
      notifyListeners();
    } catch (e) {
      fail('Could not start Design: $e');
    }
  }

  void fail(String error) {
    ready = false;
    busy = false;
    message = error;
    for (final p in pending.values) {
      if (!p.isCompleted) p.completeError(StateError(error));
    }
    pending.clear();
    if (!stopped) notifyListeners();
  }

  Future<Json> request(String command, [Json params = const {}]) async {
    if (process == null || stopped || (!ready && command != 'hello')) {
      throw StateError(
        'The Design engine is not running. Restart it before requesting work.',
      );
    }
    final id = ++sequence;
    final completer = Completer<Json>();
    pending[id] = completer;
    process!.stdin.writeln(
      jsonEncode({
        'id': id,
        'command': command,
        'params': params,
        'revision': state['revision'],
      }),
    );
    try {
      return await completer.future.timeout(const Duration(seconds: 120));
    } on TimeoutException {
      pending.remove(id);
      fail(
        'The Design engine did not respond. Restart the evaluation app before editing again.',
      );
      rethrow;
    }
  }

  Future<bool> editGuide(int revision, Json action) async {
    if (busy || !ready || readOnly) return false;
    if (state['revision'] != revision) {
      message = 'Drawing changed. Try the guide action again.';
      notifyListeners();
      return false;
    }
    busy = true;
    notifyListeners();
    try {
      final result = await request('design.guides_preview', {
        'actions': [action],
      });
      if (result['ok'] != true) throw StateError(result['error'].toString());
      if (state['revision'] != revision) {
        throw StateError('Drawing changed. Try the guide action again.');
      }
      busy = false;
      return await run('design.guides_apply', {
        'token': result['preview']['token'],
      });
    } catch (e) {
      message = e.toString();
      return false;
    } finally {
      busy = false;
      if (!stopped) notifyListeners();
    }
  }

  void showMessage(String value) {
    message = value;
    if (!stopped) notifyListeners();
  }

  Future<bool> editDimension(int revision, Json annotation) async {
    if (busy || !ready || readOnly) return false;
    if (state['revision'] != revision) {
      message = 'Drawing changed. Try the dimension action again.';
      notifyListeners();
      return false;
    }
    busy = true;
    notifyListeners();
    try {
      final result = await request('design.dimension_preview', {
        'id': annotation['id'],
        'annotation': annotation,
      });
      if (result['ok'] != true) throw StateError(result['error'].toString());
      if (state['revision'] != revision) {
        throw StateError('Drawing changed. Try the dimension action again.');
      }
      busy = false;
      return await run('design.dimension_apply', {
        'token': result['preview']['token'],
      });
    } catch (e) {
      message = e.toString();
      return false;
    } finally {
      busy = false;
      if (!stopped) notifyListeners();
    }
  }

  Future<bool> run(String command, [Json params = const {}]) async {
    if (busy || !ready) return false;
    busy = true;
    message = 'Working…';
    notifyListeners();
    try {
      final result = await request(command, params);
      if (result['state'] is Json) state = result['state'] as Json;
      message = result['ok'] == true
          ? '${command.split('.').last.replaceAll('_', ' ')} · ${entities.length} objects'
          : result['error'].toString();
      return result['ok'] == true;
    } catch (e) {
      message = e.toString();
      return false;
    } finally {
      busy = false;
      if (!stopped) notifyListeners();
    }
  }

  Future<void> savePreferences(Json value) async {
    preferences = value;
    if (!ready) return;
    try {
      await request('ui.save', value);
    } catch (e) {
      message = 'Layout could not be saved: $e';
      notifyListeners();
    }
  }

  bool visible(Json e) =>
      e['metadata']?['annotation_hidden'] != true &&
      layers.where((l) => l['name'] == e['layer']).firstOrNull?['visible'] !=
          false;
  bool locked(Json e) =>
      layers.where((l) => l['name'] == e['layer']).firstOrNull?['locked'] ==
      true;
  List<Json> get selectable =>
      entities.where((e) => visible(e) && !locked(e)).toList();
  Future<void> select(Set<String> ids) async {
    await run('selection.set', {'entity_ids': ids.toList()});
  }

  Future<void> shutdown() async {
    stopped = true;
    final child = process;
    if (child == null) return;
    await child.stdin.close();
    await child.exitCode.timeout(
      const Duration(seconds: 3),
      onTimeout: () async {
        child.kill();
        return child.exitCode;
      },
    );
  }

  @override
  void dispose() {
    stopped = true;
    process?.kill();
    super.dispose();
  }
}
