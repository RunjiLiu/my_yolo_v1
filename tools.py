import numpy as np
from data import *
import torch.nn as nn
import torch.nn.functional as F


class BCELoss(nn.Module):
    def __init__(self, weight=None, ignore_index=-100, reduce=None, reduction='mean'):
        super(BCELoss, self).__init__()
        self.reduction = reduction

    def forward(self, inputs, targets):
        pos_id = (targets == 1.0).float()
        neg_id = (targets == 0.0).float()
        pos_loss = -pos_id * torch.log(inputs + 1e-14)
        neg_loss = -neg_id * torch.log(1.0 - inputs + 1e-14)
        if self.reduction == 'mean':
            pos_loss = torch.mean(torch.sum(pos_loss, 1))
            neg_loss = torch.mean(torch.sum(neg_loss, 1))
            return pos_loss, neg_loss
        else:
            return pos_loss, neg_loss


class MSELoss(nn.Module):
    def __init__(self, reduction='mean'):
        super(MSELoss, self).__init__()
        self.reduction = reduction

    def forward(self, inputs, targets):
        pos_id = (targets == 1.0).float()
        neg_id = (targets == 0.0).float()
        pos_loss = pos_id * ((inputs - targets) ** 2)
        neg_loss = neg_id * (inputs ** 2)

        if self.reduction == 'mean':
            pos_loss = torch.mean(torch.sum(pos_loss, 1))
            neg_loss = torch.mean(torch.sum(neg_loss, 1))
            return pos_loss, neg_loss
        else:
            return pos_loss, neg_loss


class BCE_focal_loss(nn.Module):
    def __init__(self, weight=None, gamma=2, reduction='mean'):
        super(BCE_focal_loss, self).__init__()
        self.gamma = gamma
        self.reduction = reduction

    def forward(self, inputs, targets):
        pos_id = (targets == 1.0).float()
        neg_id = (1 - pos_id).float()
        pos_loss = -pos_id * (1.0 - inputs) ** self.gamma * torch.log(inputs + 1e-14)
        neg_loss = -neg_id * (inputs) ** self.gamma * torch.log(1.0 - inputs + 1e-14)

        if self.reduction == 'mean':
            return torch.mean(torch.sum(pos_loss + neg_loss, 1))
        else:
            return pos_loss + neg_loss


def generate_dxdywh(gt_label, w, h, s):
    xmin, ymin, xmax, ymax = gt_label[:-1]
    # compute the center, width and height

    c_x = (xmax + xmin) / 2 * w
    c_y = (ymax + ymin) / 2 * h
    box_w = (xmax - xmin) * w
    box_h = (ymax - ymin) * h

    if box_w < 1e-28 or box_h < 1e-28:
        # print('A dirty data !!!')
        return False

    # map center point of box to the grid cell
    c_x_s = c_x / s
    c_y_s = c_y / s
    grid_x = int(c_x_s)  # 求出来是框的左上角点的坐标
    grid_y = int(c_y_s)
    # compute the (x, y, w, h) for the corresponding grid cell
    tx = c_x_s - grid_x  # 求出偏移量
    ty = c_y_s - grid_y
    tw = np.log(box_w)
    th = np.log(box_h)  # 此处预测的是便宜量取指数次幂
    weight = 2.0 - (box_w / w) * (box_h / h)

    return grid_x, grid_y, tx, ty, tw, th, weight


def gt_creator(input_size, stride, label_lists=None, name='VOC'):
    if label_lists is None:
        label_lists = []
    assert len(input_size) > 0 and len(label_lists) > 0
    # prepare the all empty gt data
    batch_size = len(label_lists)
    w = input_size[1]
    h = input_size[0]

    # We  make gt labels by anchor-free method and anchor-based method.
    ws = w // stride  # 每个grid的宽度
    hs = h // stride  # 每个grid的高
    s = stride
    gt_tensor = np.zeros([batch_size, hs, ws, 1 + 1 + 4 + 1])

    # generate gt whose style is yolo-v1
    for batch_index in range(batch_size):
        for gt_label in label_lists[batch_index]:
            gt_class = int(gt_label[-1])
            result = generate_dxdywh(gt_label, w, h, s)
            if result:
                grid_x, grid_y, tx, ty, tw, th, weight = result

                if grid_x < gt_tensor.shape[2] and grid_y < gt_tensor.shape[1]:
                    gt_tensor[batch_index, grid_y, grid_x, 0] = 1.0
                    gt_tensor[batch_index, grid_y, grid_x, 1] = gt_class
                    gt_tensor[batch_index, grid_y, grid_x, 2:6] = np.array([tx, ty, tw, th])
                    gt_tensor[batch_index, grid_y, grid_x, 6] = weight

    gt_tensor = gt_tensor.reshape([batch_size, -1, 1 + 1 + 4 + 1])

    return gt_tensor


def loss(pred_conf, pred_cls, pred_txtytwth, label):
    '''

    Args:
        pred_conf:
        pred_cls: Batch, HW, C
        pred_txtytwth: Batch , 4
        label:

    Returns:

    '''
    obj = 5.0
    noobj = 1

    conf_loss_function = MSELoss(reduction='mean')
    cls_loss_function = nn.CrossEntropyLoss(reduction='none')
    txty_loss_function = nn.BCEWithLogitsLoss(reduction='none')
    twth_loss_function = nn.MSELoss(reduction='none')

    pred_conf = torch.sigmoid(pred_conf[:, :, 0])

    pred_cls = pred_cls.permute(0, 2, 1)
    pred_txty = pred_txtytwth[:, :, :2]  # 中心位置
    pred_twth = pred_txtytwth[:, :, 2:]  # 偏移量

    gt_obj = label[:, :, 0].float()  # 有无物体

    gt_cls = label[:, :, 1].long()  # target这里应该就是一个one-hot向量
    gt_txtytwth = label[:, :, 2:-1].float()
    gt_box_scale_weight = label[:, :, -1]

    # objectness loss
    pos_loss, neg_loss = conf_loss_function(pred_conf, gt_obj)
    conf_loss = obj * pos_loss + noobj * neg_loss

    # class loss
    cls_loss = torch.mean(torch.sum(cls_loss_function(pred_cls, gt_cls) * gt_obj, 1))

    # box loss
    txty_loss = torch.mean(
        torch.sum(torch.sum(txty_loss_function(pred_txty, gt_txtytwth[:, :, :2]), 2) * gt_box_scale_weight * gt_obj, 1))
    # 这里tx, ty多使用了sigmoid
    twth_loss = torch.mean(
        torch.sum(torch.sum(twth_loss_function(pred_twth, gt_txtytwth[:, :, 2:]), 2) * gt_box_scale_weight * gt_obj, 1))

    txtytwth_loss = txty_loss + twth_loss

    total_loss = conf_loss + cls_loss + txtytwth_loss

    return conf_loss, cls_loss, txtytwth_loss, total_loss


if __name__ == "__main__":
    pass
