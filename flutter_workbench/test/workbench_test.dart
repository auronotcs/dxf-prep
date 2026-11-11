import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/main.dart';
import 'package:workbench_prototype/model.dart';

void main() {
  test(
    'Changing setup invalidates demo review; reset restores sample state',
    () {
      final model = WorkbenchModel();
      model.update(() => model.reviewed = true);
      model.update(() => model.material = 'Aluminum', invalidates: true);
      expect(model.reviewed, isFalse);
      model.update(() {
        model.layers[0].visible = false;
        model.sheet = 1;
      });
      model.reset();
      expect(model.material, 'Cast acrylic');
      expect(model.layers.every((l) => l.visible), isTrue);
      expect(model.sheet, 0);
    },
  );

  testWidgets('Desktop layer, plate, operation, review and theme flow', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1440, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(const WorkbenchApp());
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    await tester.tap(find.byKey(const ValueKey('layer-0')));
    await tester.pumpAndSettle();
    expect(find.byTooltip('Show Letter outlines'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('plate-1')));
    await tester.pumpAndSettle();
    expect(find.text('Plate 2'), findsNWidgets(2));
    await tester.tap(find.text('Manufacture'));
    await tester.pumpAndSettle();
    expect(find.text('Include operation'), findsOneWidget);
    await tester.tap(find.text('Review setup'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Mark demo reviewed'));
    await tester.tap(find.text('Mark demo reviewed'));
    await tester.pumpAndSettle();
    expect(find.text('Demo review complete'), findsOneWidget);
    await tester.tap(find.byTooltip('Use dark theme'));
    await tester.pumpAndSettle();
    expect(find.byTooltip('Use light theme'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  for (final size in [
    const Size(1280, 720),
    const Size(920, 700),
    const Size(600, 700),
  ]) {
    testWidgets('No overflow at ${size.width} by ${size.height}', (
      tester,
    ) async {
      tester.view.physicalSize = size;
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      await tester.pumpWidget(const WorkbenchApp());
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      await tester.tap(find.text('Manufacture'));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      await tester.tap(find.text('Review'));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
    });
  }
}
