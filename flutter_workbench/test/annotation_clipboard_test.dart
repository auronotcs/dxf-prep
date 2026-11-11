import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/clipboard_dialog.dart';
import 'package:workbench_prototype/design/workspace.dart';
import 'clipboard_test.dart' show ClipboardController;

void main() {
  testWidgets(
    'Annotation picker is explicit and locked guides can copy but cannot cut',
    (t) async {
      final m = ClipboardController();
      addTearDown(m.dispose);
      m.state['guides'] = <Json>[
        {
          'id': 'g',
          'orientation': 'vertical',
          'position': 5,
          'visible': true,
          'locked': true,
          'name': 'Datum',
        },
      ];
      Json? chosen;
      await t.pumpWidget(
        MaterialApp(
          home: Builder(
            builder: (context) => TextButton(
              onPressed: () async {
                chosen = await chooseClipboardItems(context, m);
              },
              child: const Text('Choose'),
            ),
          ),
        ),
      );
      await t.tap(find.text('Choose'));
      await t.pumpAndSettle();
      await t.tap(find.text('vertical guide at 5 in'));
      await t.pumpAndSettle();
      expect(
        t
            .widget<OutlinedButton>(find.widgetWithText(OutlinedButton, 'Cut'))
            .onPressed,
        isNull,
      );
      await t.tap(find.text('Copy'));
      await t.pumpAndSettle();
      expect(chosen, {
        'cut': false,
        'include_geometry': false,
        'dimension_ids': [],
        'guide_ids': ['g'],
      });
    },
  );

  testWidgets(
    'Ctrl+C targets selected annotation; Edit exposes the mixed clipboard picker',
    (t) async {
      final m = ClipboardController();
      addTearDown(m.dispose);
      await t.pumpWidget(DesignApp(controller: m, startBackend: false));
      await t.pumpAndSettle();
      final dynamic workspace = t.state(find.byType(DesignWorkspace));
      workspace.view.selectedDimensionId = 'dim';
      await t.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
      await t.sendKeyEvent(LogicalKeyboardKey.keyC);
      await t.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
      await t.pumpAndSettle();
      expect(m.calls.single.$1, 'design.copy');
      expect(m.calls.single.$2, {
        'include_geometry': false,
        'dimension_ids': ['dim'],
      });
      await t.tap(find.byTooltip('Edit menu'));
      await t.pumpAndSettle();
      expect(find.text('Copy dimensions / guides...'), findsOneWidget);
    },
  );

  test(
    'Real independent backend sessions share clipboard and retain separate undo/recovery',
    () async {
      final base = Directory('build/tab-tests')..createSync(recursive: true);
      final folder = base.createTempSync('clipboard-');
      final clipboard = DrawingClipboard();
      final a = DesignController(
        recoveryEnabled: true,
        recoveryDirectory: '${folder.absolute.path}/recovery',
      )..drawingClipboard = clipboard;
      final b = DesignController(
        recoveryEnabled: true,
        recoveryDirectory: '${folder.absolute.path}/recovery',
      )..drawingClipboard = clipboard;
      try {
        await a.start();
        await b.start();
        expect(a.ready && b.ready, true, reason: '${a.message} ${b.message}');
        expect(a.process!.pid != b.process!.pid, true);
        await a.run('geometry.add_rectangle', {
          'x': 1,
          'y': 2,
          'width': 10,
          'height': 8,
        });
        final selected = a.selection;
        await a.copyDrawing();
        expect(b.hasClipboard, true);
        expect(await b.pasteDrawing(), true, reason: b.message);
        expect(a.entities.length, 1);
        expect(b.entities.length, 1);
        expect(a.entities.single['id'], isNot(b.entities.single['id']));
        expect(a.selection, selected);
        final copies = Directory('${folder.path}/recovery')
            .listSync()
            .whereType<File>()
            .where((f) => f.path.endsWith('.json'))
            .toList();
        expect(copies.length, 2);
        await b.run('history.undo');
        expect(b.entities, isEmpty);
        expect(a.entities.length, 1);
        expect(a.state['canUndo'], true);
        await a.shutdown();
        expect(
          await b.pasteDrawing(),
          true,
          reason: b.message,
        ); // Clipboard outlives source tab.
        await b.run('project.new', {'units': 'mm'});
        expect(await b.pasteDrawing(), false);
        expect(b.entities, isEmpty);
      } finally {
        await a.shutdown();
        await b.shutdown();
        a.dispose();
        b.dispose();
        clipboard.dispose();
        folder.deleteSync(recursive: true);
      }
    },
  );
}
