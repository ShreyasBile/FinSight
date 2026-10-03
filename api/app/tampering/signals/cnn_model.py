"""The CNN architecture, shared by training and inference.

A small custom convnet over 128x128 ELA maps — deliberately NOT EfficientNetB7:
- the input is an ELA residual map, not a natural image; ImageNet pretraining
  buys little
- 4 conv blocks (~300K params) trains in minutes on CPU and runs in
  milliseconds, which matters for a real-time lending flow
- small models overfit less on the small datasets this starts from

Output: 2 logits (0 = genuine, 1 = tampered). Dropout in the head doubles as
the MC-Dropout mechanism at inference (forward passes with train-mode dropout).
"""
import torch
import torch.nn as nn


class TamperCNN(nn.Module):
    def __init__(self, dropout: float = 0.3):
        super().__init__()
        def block(cin, cout):
            return nn.Sequential(
                nn.Conv2d(cin, cout, 3, padding=1),
                nn.BatchNorm2d(cout),
                nn.ReLU(inplace=True),
                nn.Conv2d(cout, cout, 3, padding=1),
                nn.BatchNorm2d(cout),
                nn.ReLU(inplace=True),
                nn.MaxPool2d(2),
            )
        self.features = nn.Sequential(
            block(3, 16),    # 128 -> 64
            block(16, 32),   # 64 -> 32
            block(32, 64),   # 32 -> 16
            block(64, 96),   # 16 -> 8
        )
        self.head = nn.Sequential(
            nn.AdaptiveAvgPool2d(1),
            nn.Flatten(),
            nn.Dropout(dropout),
            nn.Linear(96, 64),
            nn.ReLU(inplace=True),
            nn.Dropout(dropout),
            nn.Linear(64, 2),
        )
        # exposed so Grad-CAM can hook the last conv activation
        self.last_conv = self.features[-1][3]

    def forward(self, x):
        return self.head(self.features(x))
