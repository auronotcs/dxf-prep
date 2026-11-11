import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';

void main() {
  test(
    'Real process keeps a locked arrangement anchor stationary through apply and undo',
    () async {
      final model = DesignController();
      try {
        await model.start();
        expect(model.ready, isTrue, reason: model.message);
        expect(
          await model.run('geometry.add_rectangle', {
            'x': 0,
            'y': 20,
            'width': 10,
            'height': 5,
            'layer': 'Reference',
          }),
          isTrue,
        );
        final anchor = Map<String, dynamic>.from(model.entities.single);
        expect(
          await model.run('layer.set', {'name': 'Reference', 'locked': true}),
          isTrue,
        );
        expect(
          await model.run('geometry.add_circle', {
            'x': 30,
            'y': 3,
            'diameter': 4,
          }),
          isTrue,
        );
        final moving = model.entities.last['id'];
        expect(
          (model.state['lockedArrangementReferences'] as List).single['id'],
          anchor['id'],
        );
        final response = await model.request('design.preview_shapes', {
          'kind': 'arrange',
          'action': 'align_top',
          'reference': 'locked',
          'reference_item_id': anchor['id'],
        });
        expect(response['ok'], isTrue, reason: response['error']?.toString());
        expect(model.entities.last['center']['y'], 3);
        expect(
          await model.run('design.apply_shapes', {
            'token': response['preview']['token'],
          }),
          isTrue,
        );
        expect(model.entities.first, anchor);
        expect(model.entities.last['center']['y'], 23);
        expect(model.selection, {moving});
        expect(await model.run('history.undo'), isTrue);
        expect(model.entities.first, anchor);
        expect(model.entities.last['center']['y'], 3);
      } finally {
        await model.shutdown();
        model.dispose();
      }
    },
  );

  test(
    'Real Python child process edits, saves, reopens, and stops cleanly',
    () async {
      final model = DesignController();
      final folder = await Directory.systemTemp.createTemp(
        'flutter-design-test-',
      );
      try {
        await model.start();
        expect(model.ready, isTrue, reason: model.message);
        expect(model.dirty, isFalse);
        expect(
          await model.run('geometry.add_circle', {
            'x': 2,
            'y': 3,
            'diameter': 4,
          }),
          isTrue,
        );
        final id = model.entities.single['id'];
        expect(model.dirty, isTrue);
        expect(await model.run('history.undo'), isTrue);
        expect(model.entities, isEmpty);
        expect(await model.run('history.redo'), isTrue);
        final path = '${folder.path}/drawing.camjob.json';
        expect(await model.run('project.save', {'path': path}), isTrue);
        expect(model.dirty, isFalse);
        expect(await model.run('project.new'), isTrue);
        expect(await model.run('project.open', {'path': path}), isTrue);
        expect(model.entities.single['id'], id);
        expect(model.entities.single['radius'], 2);
        await model.select({id as String});
        final revision = model.state['revision'];
        final preview = await model.request('design.preview_shapes', {
          'kind': 'offset',
          'distance': .25,
        });
        expect(preview['ok'], isTrue);
        expect(model.state['revision'], revision);
        expect(model.entities.length, 1);
        expect(
          await model.run('design.apply_shapes', {
            'token': preview['preview']['token'],
          }),
          isTrue,
        );
        expect(model.entities.length, 2);
        expect(model.entities.last['radius'], 2.25);
        expect(await model.run('history.undo'), isTrue);
        expect(model.entities.length, 1);
        expect(await model.run('gcode.export'), isFalse);
        await model.select({id});
        final arrangement = await model.request('design.preview_shapes', {
          'kind': 'arrange',
          'action': 'align_right',
          'reference': 'plate',
        });
        expect(arrangement['ok'], isTrue);
        expect(
          await model.run('design.apply_shapes', {
            'token': arrangement['preview']['token'],
          }),
          isTrue,
        );
        expect(model.entities.single['center']['x'], 94);
        expect(await model.run('history.undo'), isTrue);
        expect(model.entities.single['center']['x'], 2);
        expect(await model.run('project.new'), isTrue);
        for (final line in [
          {'x1': 0, 'y1': 0, 'x2': 1, 'y2': 0},
          {'x1': 1.0001, 'y1': 0, 'x2': 2, 'y2': 0},
        ]) {
          expect(await model.run('geometry.add_line', line), isTrue);
        }
        await model.select(
          model.entities.map((e) => e['id'] as String).toSet(),
        );
        final repair = await model.request('design.preview_shapes', {
          'kind': 'repair',
          'mode': 'join',
          'tolerance': .001,
        });
        expect(repair['ok'], isTrue);
        expect(repair['preview']['joinCount'], 1);
        expect(
          await model.run('design.apply_shapes', {
            'token': repair['preview']['token'],
          }),
          isTrue,
        );
        expect(model.entities.single['kind'], 'polyline');
        expect(await model.run('history.undo'), isTrue);
        expect(model.entities.length, 2);
        expect(model.ready, isTrue);
        await model.shutdown();
        await model.process!.exitCode.timeout(const Duration(seconds: 5));
      } finally {
        await model.shutdown();
        model.dispose();
        await folder.delete(recursive: true);
      }
    },
  );
}
