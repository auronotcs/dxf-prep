import 'dart:io';
import 'dart:async';
import 'package:flutter/services.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/recovery_dialog.dart';
import 'package:workbench_prototype/design/workspace.dart';
import 'dimension_test.dart' show DimensionModel;

class RecoveryModel extends DimensionModel {
  bool reject = false;
  bool startCalled = false;
  RecoveryModel({bool corrupt = false}) {
    state['recovery'] = {
      'enabled': true,
      'warning': null,
      'candidates': [
        {
          'id': 'copy',
          'token': 'hash',
          'name': 'Interrupted drawing',
          'source': 'C:/drawings/Original.camjob.json',
          'updated': '2026-10-27T18:00:00Z',
          'units': 'mm',
          'objects': 2,
          'dimensions': 1,
          'guides': 1,
          'error': corrupt ? 'Unreadable copy; retained' : null,
        },
      ],
    };
  }
  @override
  Future<void> start() async {
    startCalled = true;
    notifyListeners();
  }

  @override
  Future<bool> run(String command, [Json params = const {}]) async {
    calls.add(command);
    payload = params;
    if (reject) {
      message = 'New empty drawing required';
      notifyListeners();
      return false;
    }
    if (command == 'recovery.restore' || command == 'recovery.discard') {
      state['recovery']['candidates'] = <Json>[];
      state['dirty'] = command == 'recovery.restore';
    }
    notifyListeners();
    return true;
  }
}

void main() {
  Future<void> mount(
    WidgetTester t,
    RecoveryModel m,
    List<bool> results,
  ) async {
    await t.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: Builder(
            builder: (context) => TextButton(
              onPressed: () async {
                results.add(await showRecoveryDialog(context, m));
              },
              child: const Text('Show'),
            ),
          ),
        ),
      ),
    );
    await t.tap(find.text('Show'));
    await t.pumpAndSettle();
  }

  testWidgets(
    'Later retains copies; recovery uses identity/token and explains Save As/history',
    (t) async {
      final m = RecoveryModel(), results = <bool>[];
      addTearDown(m.dispose);
      await mount(t, m, results);
      expect(find.textContaining('Undo history starts'), findsOneWidget);
      expect(find.textContaining('Original.camjob.json'), findsOneWidget);
      await t.tap(find.text('Later'));
      await t.pumpAndSettle();
      expect(results, [false]);
      expect(m.calls, isEmpty);
      await t.tap(find.text('Show'));
      await t.pumpAndSettle();
      await t.tap(find.text('Recover copy'));
      await t.pumpAndSettle();
      expect(results, [false, true]);
      expect(m.calls, ['recovery.restore']);
      expect(m.payload, {'id': 'copy', 'token': 'hash'});
      expect(m.dirty, true);
    },
  );
  testWidgets(
    'Discard requires confirmation and Cancel preserves the candidate',
    (t) async {
      final m = RecoveryModel(), results = <bool>[];
      addTearDown(m.dispose);
      await mount(t, m, results);
      await t.tap(find.text('Discard…'));
      await t.pumpAndSettle();
      await t.tap(find.text('Cancel'));
      await t.pumpAndSettle();
      expect(m.calls, isEmpty);
      await t.tap(find.text('Discard…'));
      await t.pumpAndSettle();
      await t.tap(find.text('Discard copy'));
      await t.pumpAndSettle();
      expect(m.calls, ['recovery.discard']);
      expect(
        find.textContaining('No interrupted-session copies'),
        findsOneWidget,
      );
      expect(results, isEmpty);
    },
  );
  testWidgets(
    'Recovery failure stays open and invalid copies cannot be restored',
    (t) async {
      final m = RecoveryModel()..reject = true, results = <bool>[];
      addTearDown(m.dispose);
      await mount(t, m, results);
      await t.tap(find.text('Recover copy'));
      await t.pumpAndSettle();
      expect(find.text('New empty drawing required'), findsOneWidget);
      expect(results, isEmpty);
      await t.tap(find.text('Later'));
      await t.pumpAndSettle();
      final invalid = RecoveryModel(corrupt: true);
      addTearDown(invalid.dispose);
      await mount(t, invalid, results);
      expect(
        t
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Recover copy'),
            )
            .onPressed,
        isNull,
      );
      expect(find.text('Unreadable copy; retained'), findsOneWidget);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Recover is disabled until the current drawing is empty and clean',
    (t) async {
      final m = RecoveryModel(), results = <bool>[];
      addTearDown(m.dispose);
      m.state['dirty'] = true;
      await mount(t, m, results);
      expect(
        find.textContaining('Recovery needs an empty drawing'),
        findsOneWidget,
      );
      expect(
        t
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Recover copy'),
            )
            .onPressed,
        isNull,
      );
      await t.tap(find.text('Later'));
      await t.pumpAndSettle();
      m.state['dirty'] = false;
      m.state['path'] = 'saved.camjob.json';
      await mount(t, m, results);
      expect(
        t
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Recover copy'),
            )
            .onPressed,
        isNull,
      );
      expect(m.calls, isEmpty);
    },
  );

  testWidgets(
    'Startup offers copies once and Later does not recur on model changes',
    (t) async {
      final m = RecoveryModel();
      addTearDown(m.dispose);
      await t.pumpWidget(DesignApp(controller: m));
      await t.pumpAndSettle();
      expect(m.startCalled, true);
      expect(find.text('Recover an unsaved drawing'), findsOneWidget);
      await t.tap(find.text('Later'));
      await t.pumpAndSettle();
      m.notifyListeners();
      await t.pumpAndSettle();
      expect(find.text('Recover an unsaved drawing'), findsNothing);
      expect(m.calls, isEmpty);
    },
  );
  testWidgets(
    'Cancelled close or file dialog retains recovery; explicit close discards only owned copy',
    (t) async {
      final m = RecoveryModel(), native = <String>[];
      m.state['dirty'] = true;
      addTearDown(m.dispose);
      t.binding.defaultBinaryMessenger.setMockMethodCallHandler(
        const MethodChannel('workbench/design'),
        (call) async {
          native.add(call.method);
          return null;
        },
      );
      addTearDown(
        () => t.binding.defaultBinaryMessenger.setMockMethodCallHandler(
          const MethodChannel('workbench/design'),
          null,
        ),
      );
      await t.pumpWidget(DesignApp(controller: m, startBackend: false));
      await t.pumpAndSettle();
      Future<void> closeRequest() {
        final done = Completer<void>();
        t.binding.channelBuffers.push(
          'workbench/design',
          const StandardMethodCodec().encodeMethodCall(
            const MethodCall('requestClose'),
          ),
          (_) => done.complete(),
        );
        return done.future;
      }

      final cancel = closeRequest();
      await t.pumpAndSettle();
      await t.tap(find.text('Cancel'));
      await t.pumpAndSettle();
      await cancel;
      expect(m.calls, isEmpty);
      expect(native, isEmpty);
      final state = t.state(find.byType(DesignWorkspace)) as dynamic;
      final open = state.performAction('open') as Future<void>;
      await t.pumpAndSettle();
      await open;
      expect(native, ['open']);
      expect(m.calls, isEmpty);
      expect(m.dirty, true);
      final discard = closeRequest();
      await t.pumpAndSettle();
      await t.tap(find.text('Discard'));
      await t.pumpAndSettle();
      await discard;
      expect(m.calls, ['recovery.clear']);
      expect(native, ['open', 'close']);
    },
  );

  test(
    'Real process crash keeps durable recovery; next process restores dirty with Save As',
    () async {
      final base = Directory('build/recovery-tests')
        ..createSync(recursive: true);
      final temp = base.createTempSync('session-');
      final recovery = Directory('${temp.absolute.path}/copies');
      final first = DesignController(
        recoveryEnabled: true,
        recoveryDirectory: recovery.path,
      );
      final second = DesignController(
        recoveryEnabled: true,
        recoveryDirectory: recovery.path,
      );
      try {
        await first.start();
        expect(first.ready, true, reason: first.message);
        await first.run('project.new', {'units': 'mm'});
        await first.run('geometry.add_rectangle', {
          'x': 1,
          'y': 2,
          'width': 30,
          'height': 20,
        });
        final original = '${temp.absolute.path}/original.camjob.json';
        await first.run('project.save', {'path': original});
        final originalBytes = File(original).readAsBytesSync();
        await first.run('geometry.transform', {'dx': 5, 'dy': 7});
        final expected = first.entities;
        expect(
          recovery.listSync().whereType<File>().where(
            (f) => f.path.endsWith('.json'),
          ),
          hasLength(1),
        );
        first.process!.kill();
        await first.process!.exitCode.timeout(const Duration(seconds: 10));
        await second.start();
        expect(second.ready, true, reason: second.message);
        final candidate = second.state['recovery']['candidates'][0] as Json;
        expect(
          await second.run('recovery.restore', {
            'id': candidate['id'],
            'token': candidate['token'],
          }),
          true,
          reason: second.message,
        );
        expect(second.entities, expected);
        expect(second.units, 'mm');
        expect(second.dirty, true);
        expect(second.state['savedPath'], isNull);
        expect(second.state['canUndo'], false);
        expect(File(original).readAsBytesSync(), originalBytes);
        expect(await second.run('project.save', {'path': original}), false);
        expect(
          await second.run('project.save', {
            'path': '${temp.absolute.path}/saved-copy.camjob.json',
          }),
          true,
        );
        expect(second.dirty, false);
        expect(
          recovery.listSync().whereType<File>().where(
            (f) => f.path.endsWith('.json'),
          ),
          isEmpty,
        );
      } finally {
        await first.shutdown();
        first.dispose();
        await second.shutdown();
        second.dispose();
        temp.deleteSync(recursive: true);
      }
    },
  );
}
