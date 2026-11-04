"""Small causal temporal convolutional network and binary focal loss."""
import torch
import numpy as np
from torch import nn
from torch.nn import functional as F


class CausalBlock(nn.Module):
    def __init__(self, inputs, channels, dilation):
        super().__init__()
        self.left = 2 * dilation
        self.first = nn.Conv1d(inputs, channels, 3, dilation=dilation)
        self.second = nn.Conv1d(channels, channels, 3, dilation=dilation)
        self.residual = nn.Conv1d(inputs, channels, 1) if inputs != channels else nn.Identity()
        self.dropout = nn.Dropout(.1)

    def forward(self, x):
        h = self.dropout(F.relu(self.first(F.pad(x, (self.left, 0)))))
        h = self.dropout(F.relu(self.second(F.pad(h, (self.left, 0)))))
        return F.relu(h + self.residual(x))


class CrisisTCN(nn.Module):
    def __init__(self, features, channels=16):
        super().__init__()
        self.network = nn.Sequential(CausalBlock(features, channels, 1),
                                     CausalBlock(channels, channels, 2),
                                     CausalBlock(channels, channels, 4))
        self.head = nn.Linear(channels, 1)

    def forward(self, x):
        return self.head(self.network(x.transpose(1, 2))[:, :, -1]).squeeze(-1)


def focal_loss(logits, targets, alpha=.75, gamma=2.):
    bce = F.binary_cross_entropy_with_logits(logits, targets, reduction="none")
    probability_correct = torch.exp(-bce)
    weight = torch.where(targets > .5, alpha, 1. - alpha)
    return (weight * (1 - probability_correct).pow(gamma) * bce).mean()


def probabilities(model, x, batch_size=1024):
    model.eval()
    values = []
    with torch.no_grad():
        for batch in torch.split(torch.from_numpy(x), batch_size):
            values.append(torch.sigmoid(model(batch)).numpy())
    return np.concatenate(values)
