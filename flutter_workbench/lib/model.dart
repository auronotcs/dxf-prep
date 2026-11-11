import 'package:flutter/material.dart';

const ink = Color(0xff203338);
const teal = Color(0xff087f75);
const muted = Color(0xff728184);
const line = Color(0xffdce3e1);

class DemoLayer {
  DemoLayer(this.name, this.color, this.count, {this.visible = true});
  final String name;
  final Color color;
  final int count;
  bool visible;
}

class DemoOperation {
  DemoOperation(this.name, this.detail, this.icon, {this.enabled = true});
  final String name;
  final String detail;
  final IconData icon;
  bool enabled;
}

class WorkbenchModel extends ChangeNotifier {
  int workspace = 0;
  int selected = -1;
  int selectedOperation = 0;
  int sheet = 0;
  bool dark = false;
  bool grid = true;
  bool paths = false;
  bool pan = false;
  bool reviewed = false;
  bool layersOpen = true;
  String material = 'Cast acrylic';
  double thickness = 0.5;
  double depth = 0.125;
  double feed = 120;
  final layers = [
    DemoLayer('Letter outlines', const Color(0xff138c84), 5),
    DemoLayer('Mounting holes', const Color(0xff7976c4), 10),
    DemoLayer('Reference frame', const Color(0xffbd965c), 1),
  ];
  final operations = [
    DemoOperation('Pocket clearing', 'T4 · ¼″ end mill', Icons.blur_linear),
    DemoOperation('Mounting holes', 'T2 · ⅛″ drill', Icons.control_point),
    DemoOperation('Outside profile', 'T4 · ¼″ end mill', Icons.crop_free),
  ];

  void update(VoidCallback change, {bool invalidates = false}) {
    change();
    if (invalidates) reviewed = false;
    notifyListeners();
  }

  void reset() {
    selected = -1;
    sheet = 0;
    material = 'Cast acrylic';
    thickness = 0.5;
    depth = 0.125;
    feed = 120;
    paths = false;
    reviewed = false;
    for (final layer in layers) {
      layer.visible = true;
    }
    for (final operation in operations) {
      operation.enabled = true;
    }
    notifyListeners();
  }
}
