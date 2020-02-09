# code-checked
# server-checked

import torch
import torch.nn as nn
from torch.autograd import Variable
import torch.nn.functional as F
from torch.utils import data

import os
import numpy as np
import cv2

from datasets import DatasetCityscapesEval
from models.model_mcdropout import get_model

from utils.utils import label_img_2_color, get_confusion_matrix

model_id = "mcdropout_0"
M = 8

data_dir = "../data/cityscapes"
data_list = "lists/cityscapes/val.lst"
batch_size = 2
num_classes = 19
max_entropy = np.log(num_classes)

eval_dataset = DatasetCityscapesEval(root=data_dir, list_path=data_list)
eval_loader = data.DataLoader(eval_dataset, batch_size=batch_size, shuffle=False, pin_memory=True)

output_path = "training_logs/%s_M%d_eval" % (model_id, M)
if not os.path.exists(output_path):
    os.makedirs(output_path)

restore_from = "trained_models/%s/checkpoint_60000.pth" % model_id
deeplab = get_model(num_classes=num_classes)
deeplab.load_state_dict(torch.load(restore_from))
model = nn.DataParallel(deeplab)
model.eval()
model.cuda()

M_float = float(M)
print (M_float)

confusion_matrix = np.zeros((num_classes, num_classes))
for step, batch in enumerate(eval_loader):
    with torch.no_grad():
        print ("%d/%d" % (step+1, len(eval_loader)))

        image, label, _, name = batch
        # (image has shape: (batch_size, 3, h, w))
        # (label has shape: (batch_size, h, w))

        batch_size = image.size(0)
        h = image.size(2)
        w = image.size(3)

        p = torch.zeros(batch_size, num_classes, h, w).cuda() # (shape: (batch_size, num_classes, h, w))
        for i in range(M):
            logits_downsampled = model(Variable(image).cuda()) # (shape: (batch_size, num_classes, h/8, w/8))
            logits = F.upsample(input=logits_downsampled , size=(h, w), mode='bilinear', align_corners=True) # (shape: (batch_size, num_classes, h, w))
            p_value = F.softmax(logits, dim=1) # (shape: (batch_size, num_classes, h, w))
            p = p + p_value/M_float

        p_numpy = p.cpu().data.numpy().transpose(0, 2, 3, 1) # (array of shape: (batch_size, h, w, num_classes))

        seg_pred = np.argmax(p_numpy, axis=3).astype(np.uint8)
        m_seg_pred = np.ma.masked_array(seg_pred, mask=torch.eq(label, 255))
        np.ma.set_fill_value(m_seg_pred, 20)
        seg_pred = m_seg_pred

        seg_gt = label.numpy().astype(np.int)
        ignore_index = seg_gt != 255
        seg_gt = seg_gt[ignore_index]
        seg_pred = seg_pred[ignore_index]
        confusion_matrix += get_confusion_matrix(seg_gt, seg_pred, num_classes)

        entropy = -np.sum(p_numpy*np.log(p_numpy), axis=3) # (shape: (batch_size, h, w))
        pred_label_imgs_raw = np.argmax(p_numpy, axis=3).astype(np.uint8)
        for i in range(image.size(0)):
            if i == 0:
                img = image[i].data.cpu().numpy()
                img = np.transpose(img, (1, 2, 0)) # (shape: (img_h, img_w, 3))
                img = img + np.array([102.9801, 115.9465, 122.7717])
                img = img[:,:,::-1]
                cv2.imwrite(output_path + "/" + name[i] + "_img.png", img)

                label_img = label[i].data.cpu().numpy()
                label_img = label_img.astype(np.uint8)
                label_img_color = label_img_2_color(label_img)[:,:,::-1]
                overlayed_img = 0.30*img + 0.70*label_img_color
                overlayed_img = overlayed_img.astype(np.uint8)
                cv2.imwrite(output_path + "/" + name[i] + "_label_overlayed.png", overlayed_img)

                pred_label_img = pred_label_imgs_raw[i]
                pred_label_img = pred_label_img.astype(np.uint8)
                pred_label_img_color = label_img_2_color(pred_label_img)[:,:,::-1]
                overlayed_img = 0.30*img + 0.70*pred_label_img_color
                overlayed_img = overlayed_img.astype(np.uint8)
                cv2.imwrite(output_path + "/" + name[i] + "_pred_overlayed.png", overlayed_img)

                entropy_img = entropy[i]
                entropy_img = (entropy_img/max_entropy)*255
                entropy_img = entropy_img.astype(np.uint8)
                entropy_img = cv2.applyColorMap(entropy_img, cv2.COLORMAP_HOT)
                cv2.imwrite(output_path + "/" + name[i] + "_entropy.png", entropy_img)

        # # # # # # # # # # # # # # # # # # debug START:
        # if step > 0:
        #     break
        # # # # # # # # # # # # # # # # # # debug END:

pos = confusion_matrix.sum(1)
res = confusion_matrix.sum(0)
tp = np.diag(confusion_matrix)

IU_array = (tp / np.maximum(1.0, pos + res - tp))
mean_IU = IU_array.mean()
print({'meanIU':mean_IU, 'IU_array':IU_array})
