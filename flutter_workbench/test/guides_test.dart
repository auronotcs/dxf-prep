import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/canvas.dart';
import 'package:workbench_prototype/design/guides_dialog.dart';

class GuideModel extends DesignController {
  final calls = <String>[];
  final payloads = <Json>[];
  GuideModel() {
    ready = true;
    busy = false;
    state = {
      'revision': 1,
      'units': 'mm',
      'guideAuthoring': true,
      'entities': <Json>[],
      'guides': <Json>[],
      'selected': ['original'],
      'layers': <Json>[],
    };
  }
  @override
  Future<Json> request(String command, [Json params = const {}]) async {
    calls.add(command);
    payloads.add(params);
    final rows = (state['guides'] as List)
        .map((g) => Map<String, dynamic>.from(g as Map))
        .toList();
    for (final a in params['actions'] as List) {
      if (a['operation'] == 'add') {
        rows.add({
          ...a,
          'visible': true,
          'locked': false,
          'name': a['name'] ?? '',
        });
      } else {
        final row = rows.where((g) => g['id'] == a['id']).firstOrNull;
        if (a['operation'] == 'delete') {
          rows.remove(row);
        }
        if (a['operation'] == 'locked') row!['locked'] = a['value'];
        if (a['operation'] == 'visible') row!['visible'] = a['value'];
        if (a['operation'] == 'edit') {
          row!['position'] = a['position'];
          row['name'] = a['name'];
        }
      }
    }
    return {
      'ok': true,
      'preview': {
        'guides': rows,
        'revision': state['revision'],
        'token': 'guide-token',
      },
    };
  }

  @override
  Future<bool> run(String command, [Json params = const {}]) async {
    calls.add(command);
    payloads.add(params);
    return true;
  }
}

void main() {
  Future<void> mount(
    WidgetTester tester,
    GuideModel m,
    DesignViewport view,
  ) async {
    tester.view.physicalSize = const Size(820, 520);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(m.dispose);
    addTearDown(view.dispose);
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) => Scaffold(
            body: TextButton(
              onPressed: () => showDialog<void>(
                context: context,
                builder: (_) => GuidesDialog(model: m, view: view),
              ),
              child: const Text('Open'),
            ),
          ),
        ),
      ),
    );
    await tester.tap(find.text('Open'));
    await tester.pumpAndSettle();
  }

  Future<void> tap(WidgetTester tester, String label) async {
    await tester.ensureVisible(find.text(label));
    await tester.tap(find.text(label));
    await tester.pumpAndSettle();
  }

  testWidgets(
    'Guide manager drafts exact coordinates, unlocks edits, and applies once in compact windows',
    (tester) async {
      final m = GuideModel(), view = DesignViewport();
      await mount(tester, m, view);
      await tester.ensureVisible(
        find.byKey(const ValueKey('guide-coordinate')),
      );
      await tester.enterText(
        find.byKey(const ValueKey('guide-coordinate')),
        '25.4',
      );
      await tap(tester, 'Add guide');
      expect(m.payloads.last['actions'][0]['position'], 25.4);
      expect(m.calls.contains('design.guides_apply'), false);
      await tap(tester, 'Lock / Unlock');
      expect(
        tester
            .widget<TextButton>(
              find.widgetWithText(TextButton, 'Update coordinate / name'),
            )
            .onPressed,
        isNull,
      );
      expect(
        tester
            .widget<TextButton>(
              find.widgetWithText(TextButton, 'Delete from draft'),
            )
            .onPressed,
        isNull,
      );
      await tap(tester, 'Lock / Unlock');
      await tester.ensureVisible(
        find.byKey(const ValueKey('guide-coordinate')),
      );
      await tester.enterText(
        find.byKey(const ValueKey('guide-coordinate')),
        '50.8',
      );
      await tap(tester, 'Update coordinate / name');
      expect(m.payloads.last['actions'].last['position'], 50.8);
      await tap(tester, 'Apply');
      expect(m.calls.last, 'design.guides_apply');
      expect(m.payloads.last, {'token': 'guide-token'});
      expect(m.selection, {'original'});
      expect(tester.takeException(), isNull);
    },
  );
  testWidgets(
    'View visibility is independent; cancellation and stale drawings cannot commit',
    (tester) async {
      final m = GuideModel(), view = DesignViewport();
      await mount(tester, m, view);
      await tap(tester, 'Show guides on canvas');
      expect(view.guidesShown, false);
      expect(m.calls, isEmpty);
      await tap(tester, 'Add guide');
      m.state['revision'] = 2;
      m.notifyListeners();
      await tester.pumpAndSettle();
      expect(
        tester
            .widget<FilledButton>(find.widgetWithText(FilledButton, 'Apply'))
            .onPressed,
        isNull,
      );
      await tap(tester, 'Cancel');
      expect(m.calls.contains('design.guides_apply'), false);
    },
  );
  testWidgets('Machining jobs expose view toggle but no editing', (
    tester,
  ) async {
    final m = GuideModel()..state['readOnly'] = true, view = DesignViewport();
    await mount(tester, m, view);
    expect(
      tester
          .widget<TextButton>(find.widgetWithText(TextButton, 'Add guide'))
          .onPressed,
      isNull,
    );
    await tap(tester, 'Show guides on canvas');
    expect(view.guidesShown, false);
  });
  test(
    'Guide snapping uses screen tolerance, visibility and closest candidate; lock retains snapping',
    () {
      final v = DesignViewport()
        ..scale = 100
        ..snap = true
        ..guides = [
          {
            'orientation': 'vertical',
            'position': 4.25,
            'visible': true,
            'locked': true,
          },
        ];
      addTearDown(v.dispose);
      expect(v.snapWorld(const Offset(4.28, 2.3)), const Offset(4.25, 2.3));
      expect(v.snapWorld(const Offset(4.5, 2.3)), const Offset(4.5, 2.25));
      v.guidesShown = false;
      expect(v.snapWorld(const Offset(4.28, 2.3)), const Offset(4.25, 2.25));
      v.guidesShown = true;
      v.guides[0]['visible'] = false;
      expect(v.snapWorld(const Offset(4.28, 2.3)), const Offset(4.25, 2.25));
      v.guides = [
        {
          'orientation': 'horizontal',
          'position': 2.25,
          'visible': true,
          'locked': false,
        },
      ];
      expect(v.snapWorld(const Offset(4.3, 2.28)), const Offset(4.3, 2.25));
      v.snap = false;
      expect(v.snapWorld(const Offset(4.3, 2.28)), const Offset(4.3, 2.28));
    },
  );
  testWidgets(
    'Both rulers create guides; outside drop and cancellation create nothing',
    (tester) async {
      final m = GuideModel(),
          v = DesignViewport()
            ..scale = 10
            ..translation = const Offset(100, 300);
      addTearDown(m.dispose);
      addTearDown(v.dispose);
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: DesignCanvas(model: m, view: v, layer: '0', dark: false),
          ),
        ),
      );
      await tester.dragFrom(const Offset(200, 10), const Offset(0, 140));
      await tester.pumpAndSettle();
      expect(m.calls.last, 'design.guide_add');
      expect(m.payloads.last, {'orientation': 'horizontal', 'position': 15.0});
      await tester.dragFrom(const Offset(10, 100), const Offset(140, 0));
      await tester.pumpAndSettle();
      expect(m.payloads.last, {'orientation': 'vertical', 'position': 5.0});
      final before = m.calls.length;
      await tester.dragFrom(const Offset(200, 10), const Offset(-300, 140));
      await tester.pumpAndSettle();
      expect(m.calls.length, before);
      final gesture = await tester.startGesture(const Offset(200, 10));
      await gesture.moveTo(const Offset(200, 150));
      v.setTool('select');
      await gesture.up();
      await tester.pumpAndSettle();
      expect(m.calls.length, before);
      final cancelled = await tester.startGesture(const Offset(10, 100));
      await cancelled.moveTo(const Offset(150, 100));
      await cancelled.cancel();
      await tester.pumpAndSettle();
      expect(m.calls.length, before);
      expect(v.guideOrientation, isNull);
    },
  );
  testWidgets(
    'Drawing clicks snap to a visible guide without changing geometry selection',
    (tester) async {
      final m = GuideModel();
      m.state['guides'] = [
        {
          'orientation': 'vertical',
          'position': 4.25,
          'visible': true,
          'locked': true,
        },
      ];
      final v = DesignViewport()
        ..scale = 100
        ..translation = const Offset(100, 400)
        ..snap = true
        ..setTool('line');
      addTearDown(m.dispose);
      addTearDown(v.dispose);
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: DesignCanvas(model: m, view: v, layer: '0', dark: false),
          ),
        ),
      );
      await tester.tapAt(const Offset(528, 170));
      await tester.pumpAndSettle();
      await tester.tapAt(const Offset(528, 270));
      await tester.pumpAndSettle();
      expect(m.calls.last, 'geometry.add_line');
      expect(m.payloads.last['x1'], 4.25);
      expect(m.payloads.last['x2'], 4.25);
      expect(m.payloads.last['y1'], 2.3);
      expect(m.selection, {'original'});
    },
  );
}
