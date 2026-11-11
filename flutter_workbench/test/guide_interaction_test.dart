import 'dart:async';
import 'dart:io';
import 'dart:convert';
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/canvas.dart';
import 'package:workbench_prototype/design/guide_actions.dart';
import 'guides_test.dart' show GuideModel;

class DirectGuideModel extends GuideModel {
  Json? preview;
  Completer<void>? gate;
  @override
  Future<Json> request(String command, [Json params = const {}]) async {
    final result = await super.request(command, params);
    preview = result['preview'];
    if (gate != null) await gate!.future;
    return result;
  }

  @override
  Future<bool> run(String command, [Json params = const {}]) async {
    await super.run(command, params);
    if (command == 'design.guides_apply') {
      state['guides'] = preview!['guides'];
      state['revision']++;
      notifyListeners();
    }
    return true;
  }
}

Json guide(
  String id,
  String orientation,
  double position, {
  bool locked = false,
  bool visible = true,
}) => {
  'id': id,
  'orientation': orientation,
  'position': position,
  'locked': locked,
  'visible': visible,
  'name': 'Datum',
};

void main() {
  Future<void> mount(
    WidgetTester tester,
    DirectGuideModel m,
    DesignViewport v,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: AnimatedBuilder(
            animation: m,
            builder: (_, _) =>
                DesignCanvas(model: m, view: v, layer: '0', dark: false),
          ),
        ),
      ),
    );
    await tester.pump();
  }

  testWidgets(
    'Both guide orientations preview then apply once and preserve selection',
    (tester) async {
      final m = DirectGuideModel(),
          v = DesignViewport()
            ..scale = 10
            ..translation = const Offset(100, 300);
      addTearDown(m.dispose);
      addTearDown(v.dispose);
      m.state['guides'] = [
        guide('h', 'horizontal', 5),
        guide('v', 'vertical', 10),
      ];
      await mount(tester, m, v);
      final mouse = await tester.createGesture(kind: PointerDeviceKind.mouse);
      await mouse.addPointer(location: const Offset(400, 251));
      await mouse.moveTo(const Offset(401, 251));
      await tester.pump();
      expect(v.hoveredGuideId, 'h');
      final g = await tester.startGesture(const Offset(400, 251));
      await g.moveTo(const Offset(400, 221));
      await tester.pump();
      expect(v.guidePosition, closeTo(8, 1e-9));
      expect(m.state['guides'][0]['position'], 5);
      expect(m.calls, isEmpty);
      await g.up();
      await tester.pumpAndSettle();
      expect(m.calls, ['design.guides_preview', 'design.guides_apply']);
      expect(m.state['guides'][0]['position'], closeTo(8, 1e-9));
      expect(m.selection, {'original'});
      m.calls.clear();
      m.payloads.clear();
      final vertical = await tester.startGesture(const Offset(202, 400));
      await vertical.moveTo(const Offset(172, 400));
      await vertical.up();
      await tester.pumpAndSettle();
      expect(m.state['guides'][1]['position'], closeTo(7, 1e-9));
      expect(m.calls, ['design.guides_preview', 'design.guides_apply']);
      expect(v.draggedGuideId, isNull);
      expect(tester.takeException(), isNull);
    },
  );
  testWidgets(
    'Locked, hidden, globally hidden and view-only guides cannot be dragged',
    (tester) async {
      final m = DirectGuideModel(),
          v = DesignViewport()
            ..scale = 10
            ..translation = const Offset(100, 300);
      addTearDown(m.dispose);
      addTearDown(v.dispose);
      for (final mode in ['locked', 'hidden', 'global', 'readonly']) {
        m.calls.clear();
        m.state['guides'] = [
          guide(
            'h',
            'horizontal',
            5,
            locked: mode == 'locked',
            visible: mode != 'hidden',
          ),
        ];
        m.state['readOnly'] = mode == 'readonly';
        v.guidesShown = mode != 'global';
        await mount(tester, m, v);
        final g = await tester.startGesture(const Offset(400, 250));
        await g.moveTo(const Offset(400, 200));
        await g.up();
        await tester.pumpAndSettle();
        expect(
          m.calls.where((s) => s.startsWith('design.guide')),
          isEmpty,
          reason: mode,
        );
        expect(m.state['guides'][0]['position'], 5);
      }
    },
  );
  testWidgets(
    'Cancel, Escape/tool change, outside drop and stale revisions abandon guide preview',
    (tester) async {
      final m = DirectGuideModel(),
          v = DesignViewport()
            ..scale = 10
            ..translation = const Offset(100, 300);
      addTearDown(m.dispose);
      addTearDown(v.dispose);
      m.state['guides'] = [guide('h', 'horizontal', 5)];
      for (final mode in [
        'cancel',
        'escape',
        'outside',
        'revision',
        'hidden',
      ]) {
        v.guidesShown = true;
        await mount(tester, m, v);
        final g = await tester.startGesture(const Offset(400, 250));
        await g.moveTo(const Offset(400, 200));
        await tester.pump();
        if (mode == 'cancel') {
          await g.cancel();
        } else {
          if (mode == 'escape') v.setTool('select');
          if (mode == 'outside') await g.moveTo(const Offset(400, -10));
          if (mode == 'revision') m.state['revision']++;
          if (mode == 'hidden') v.guidesShown = false;
          await g.up();
        }
        await tester.pumpAndSettle();
        expect(m.calls, isEmpty, reason: mode);
        expect(v.guidePosition, isNull);
        expect(v.draggedGuideId, isNull);
      }
    },
  );
  testWidgets(
    'Moved guide never snaps back to itself and small clicks do not create history',
    (tester) async {
      final m = DirectGuideModel(),
          v = DesignViewport()
            ..snap = true
            ..scale = 100
            ..translation = const Offset(100, 500);
      addTearDown(m.dispose);
      addTearDown(v.dispose);
      m.state['units'] = 'in';
      m.state['guides'] = [guide('h', 'horizontal', 2.03)];
      await mount(tester, m, v);
      await tester.tapAt(const Offset(400, 299));
      await tester.pumpAndSettle();
      expect(m.calls, isEmpty);
      final g = await tester.startGesture(const Offset(400, 299));
      await g.moveTo(const Offset(400, 293));
      await tester.pump();
      expect(v.guidePosition, closeTo(2.125, 1e-9));
      await g.up();
      await tester.pumpAndSettle();
      expect(m.state['guides'][0]['position'], 2.125);
    },
  );
  testWidgets(
    'Right-click exact edit validates numbers; lock disables edit/delete; unlock/delete works',
    (tester) async {
      final m = DirectGuideModel(),
          v = DesignViewport()
            ..scale = 10
            ..translation = const Offset(100, 300);
      addTearDown(m.dispose);
      addTearDown(v.dispose);
      m.state['guides'] = [guide('h', 'horizontal', 5)];
      await mount(tester, m, v);
      Future<void> menu(Offset at) async {
        await tester.tapAt(at, buttons: kSecondaryMouseButton);
        await tester.pumpAndSettle();
      }

      await menu(const Offset(400, 250));
      await tester.tap(find.text('Edit guide…'));
      await tester.pumpAndSettle();
      await tester.enterText(
        find.widgetWithText(TextField, 'Y coordinate (mm)'),
        'NaN',
      );
      await tester.tap(find.text('Save guide'));
      await tester.pumpAndSettle();
      expect(find.text('Enter a finite coordinate.'), findsOneWidget);
      expect(m.calls, isEmpty);
      await tester.enterText(
        find.widgetWithText(TextField, 'Y coordinate (mm)'),
        '-3.5',
      );
      await tester.enterText(
        find.widgetWithText(TextField, 'Name (optional)'),
        'Baseline',
      );
      await tester.tap(find.text('Save guide'));
      await tester.pumpAndSettle();
      expect(m.state['guides'][0]['position'], -3.5);
      expect(m.state['guides'][0]['name'], 'Baseline');
      await menu(const Offset(400, 335));
      await tester.tap(find.text('Lock guide'));
      await tester.pumpAndSettle();
      await menu(const Offset(400, 335));
      for (final label in ['Edit guide…', 'Delete guide']) {
        final item = find.ancestor(
          of: find.text(label),
          matching: find.byType(PopupMenuItem<String>),
        );
        expect(tester.widget<PopupMenuItem<String>>(item).enabled, false);
      }
      await tester.tap(find.text('Unlock guide'));
      await tester.pumpAndSettle();
      await menu(const Offset(400, 335));
      await tester.tap(find.text('Delete guide'));
      await tester.pumpAndSettle();
      expect(m.state['guides'], isEmpty);
      expect(m.selection, {'original'});
      expect(tester.takeException(), isNull);
    },
  );
  testWidgets(
    'Cancel dialog does not commit; Hide removes the guide from hit testing',
    (tester) async {
      final m = DirectGuideModel(),
          v = DesignViewport()
            ..scale = 10
            ..translation = const Offset(100, 300);
      addTearDown(m.dispose);
      addTearDown(v.dispose);
      m.state['guides'] = [guide('h', 'horizontal', 5)];
      await mount(tester, m, v);
      await tester.tapAt(
        const Offset(400, 250),
        buttons: kSecondaryMouseButton,
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('Edit guide…'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      expect(m.calls, isEmpty);
      await tester.tapAt(
        const Offset(400, 250),
        buttons: kSecondaryMouseButton,
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('Hide guide'));
      await tester.pumpAndSettle();
      expect(m.state['guides'][0]['visible'], false);
      m.calls.clear();
      await tester.dragFrom(const Offset(400, 250), const Offset(0, -40));
      await tester.pumpAndSettle();
      expect(m.calls.where((s) => s.startsWith('design.guide')), isEmpty);
    },
  );
  test(
    'Concurrent or stale guide drafts cannot apply and release controller busy state',
    () async {
      final m = DirectGuideModel();
      addTearDown(m.dispose);
      m.state['guides'] = [guide('h', 'horizontal', 5)];
      m.gate = Completer<void>();
      final first = applyGuideAction(m, 1, {
        'operation': 'edit',
        'id': 'h',
        'position': 8,
      });
      expect(m.busy, true);
      expect(
        await applyGuideAction(m, 1, {'operation': 'delete', 'id': 'h'}),
        false,
      );
      m.state['revision'] = 2;
      m.gate!.complete();
      expect(await first, false);
      expect(m.busy, false);
      expect(m.calls, ['design.guides_preview']);
    },
  );
  test(
    'Real backend direct actions preserve IDs/geometry, one-step undo, units and save/reopen',
    () async {
      final m = DesignController(),
          tmp = Directory.systemTemp.createTempSync('guide-direct-');
      try {
        await m.start();
        expect(m.ready, true, reason: m.message);
        for (final units in ['in', 'mm']) {
          await m.run('project.new', {'units': units});
          await m.run('geometry.add_rectangle', {
            'x': 0,
            'y': 0,
            'width': 10,
            'height': 5,
          });
          final original = jsonEncode(m.entities),
              selection = Set.of(m.selection);
          await m.run('design.guide_add', {
            'orientation': 'vertical',
            'position': -2.5,
          });
          final id = m.state['guides'][0]['id'];
          final history = m.state['history']['index'];
          expect(
            await applyGuideAction(m, m.state['revision'], {
              'operation': 'edit',
              'id': id,
              'position': 7.125,
              'name': 'Datum',
            }),
            true,
          );
          expect(m.state['history']['index'], history + 1);
          expect(jsonEncode(m.entities), original);
          expect(m.selection, selection);
          await m.run('history.undo');
          expect(m.state['guides'][0]['position'], -2.5);
          await m.run('history.redo');
          expect(m.state['guides'][0]['position'], 7.125);
          final path = '${tmp.path}/$units.camjob.json';
          await m.run('project.save', {'path': path});
          await m.run('project.open', {'path': path});
          expect(m.state['guides'][0]['id'], id);
          expect(m.state['guides'][0]['name'], 'Datum');
          expect(m.state['guides'][0]['position'], 7.125);
          await applyGuideAction(m, m.state['revision'], {
            'operation': 'locked',
            'id': id,
            'value': true,
          });
          expect(
            await applyGuideAction(m, m.state['revision'], {
              'operation': 'delete',
              'id': id,
            }),
            false,
          );
          expect(m.state['guides'], hasLength(1));
          await applyGuideAction(m, m.state['revision'], {
            'operation': 'locked',
            'id': id,
            'value': false,
          });
          expect(
            await applyGuideAction(m, m.state['revision'], {
              'operation': 'delete',
              'id': id,
            }),
            true,
          );
          expect(m.state['guides'], isEmpty);
        }
      } finally {
        await m.shutdown();
        m.dispose();
        tmp.deleteSync(recursive: true);
      }
    },
  );
}
