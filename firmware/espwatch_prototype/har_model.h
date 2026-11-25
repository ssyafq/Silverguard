


    // !!! This file is generated using emlearn !!!

    #include <stdint.h>
    

static inline int32_t har_model_tree_0(const float *features, int32_t features_length) {
          if (features[2] < 0.857809f) {
              if (features[1] < 0.107171f) {
                  if (features[4] < 0.581699f) {
                      return 0;
                  } else {
                      if (features[0] < 1.016233f) {
                          return 0;
                      } else {
                          return 1;
                      }
                  }
              } else {
                  if (features[1] < 0.162076f) {
                      if (features[4] < 0.966719f) {
                          return 1;
                      } else {
                          return 0;
                      }
                  } else {
                      return 1;
                  }
              }
          } else {
              return 0;
          }
        }
        

static inline int32_t har_model_tree_1(const float *features, int32_t features_length) {
          if (features[1] < 0.077648f) {
              return 0;
          } else {
              return 1;
          }
        }
        

static inline int32_t har_model_tree_2(const float *features, int32_t features_length) {
          if (features[3] < 1.290755f) {
              if (features[1] < 0.094687f) {
                  return 0;
              } else {
                  return 1;
              }
          } else {
              if (features[0] < 1.008854f) {
                  if (features[0] < 1.008641f) {
                      return 1;
                  } else {
                      return 0;
                  }
              } else {
                  return 1;
              }
          }
        }
        

static inline int32_t har_model_tree_3(const float *features, int32_t features_length) {
          if (features[1] < 0.081301f) {
              return 0;
          } else {
              if (features[1] < 0.103266f) {
                  if (features[3] < 1.239434f) {
                      return 0;
                  } else {
                      return 1;
                  }
              } else {
                  if (features[1] < 0.162076f) {
                      if (features[1] < 0.161197f) {
                          return 1;
                      } else {
                          return 0;
                      }
                  } else {
                      return 1;
                  }
              }
          }
        }
        

static inline int32_t har_model_tree_4(const float *features, int32_t features_length) {
          if (features[3] < 1.253256f) {
              return 0;
          } else {
              if (features[1] < 0.072474f) {
                  return 0;
              } else {
                  if (features[0] < 0.986081f) {
                      if (features[4] < 1.447125f) {
                          return 0;
                      } else {
                          return 1;
                      }
                  } else {
                      if (features[0] < 1.008854f) {
                          if (features[0] < 1.008641f) {
                              return 1;
                          } else {
                              return 0;
                          }
                      } else {
                          return 1;
                      }
                  }
              }
          }
        }
        

static inline int32_t har_model_tree_5(const float *features, int32_t features_length) {
          if (features[4] < 0.412723f) {
              return 0;
          } else {
              if (features[3] < 1.281083f) {
                  if (features[4] < 0.429942f) {
                      return 1;
                  } else {
                      return 0;
                  }
              } else {
                  if (features[3] < 1.449885f) {
                      if (features[3] < 1.447937f) {
                          return 1;
                      } else {
                          return 0;
                      }
                  } else {
                      return 1;
                  }
              }
          }
        }
        

static inline int32_t har_model_tree_6(const float *features, int32_t features_length) {
          if (features[1] < 0.082209f) {
              return 0;
          } else {
              if (features[0] < 0.992618f) {
                  if (features[4] < 1.432899f) {
                      return 0;
                  } else {
                      return 1;
                  }
              } else {
                  if (features[2] < 0.362193f) {
                      if (features[2] < 0.361520f) {
                          return 1;
                      } else {
                          return 0;
                      }
                  } else {
                      return 1;
                  }
              }
          }
        }
        

static inline int32_t har_model_tree_7(const float *features, int32_t features_length) {
          if (features[4] < 0.474837f) {
              if (features[3] < 1.280601f) {
                  return 0;
              } else {
                  if (features[4] < 0.459903f) {
                      return 1;
                  } else {
                      return 0;
                  }
              }
          } else {
              if (features[3] < 1.246967f) {
                  return 0;
              } else {
                  if (features[3] < 1.449336f) {
                      if (features[0] < 1.004802f) {
                          return 0;
                      } else {
                          return 1;
                      }
                  } else {
                      return 1;
                  }
              }
          }
        }
        

static inline int32_t har_model_tree_8(const float *features, int32_t features_length) {
          if (features[3] < 1.288616f) {
              if (features[1] < 0.108394f) {
                  return 0;
              } else {
                  return 1;
              }
          } else {
              if (features[1] < 0.080981f) {
                  return 0;
              } else {
                  if (features[1] < 0.162076f) {
                      if (features[4] < 0.925524f) {
                          return 1;
                      } else {
                          return 0;
                      }
                  } else {
                      return 1;
                  }
              }
          }
        }
        

static inline int32_t har_model_tree_9(const float *features, int32_t features_length) {
          if (features[1] < 0.077648f) {
              return 0;
          } else {
              if (features[0] < 0.991455f) {
                  if (features[3] < 1.726370f) {
                      return 0;
                  } else {
                      return 1;
                  }
              } else {
                  return 1;
              }
          }
        }
        

int32_t har_model_predict(const float *features, int32_t features_length) {

        int32_t votes[2] = {0,};
        int32_t _class = -1;

        _class = har_model_tree_0(features, features_length); votes[_class] += 1;
    _class = har_model_tree_1(features, features_length); votes[_class] += 1;
    _class = har_model_tree_2(features, features_length); votes[_class] += 1;
    _class = har_model_tree_3(features, features_length); votes[_class] += 1;
    _class = har_model_tree_4(features, features_length); votes[_class] += 1;
    _class = har_model_tree_5(features, features_length); votes[_class] += 1;
    _class = har_model_tree_6(features, features_length); votes[_class] += 1;
    _class = har_model_tree_7(features, features_length); votes[_class] += 1;
    _class = har_model_tree_8(features, features_length); votes[_class] += 1;
    _class = har_model_tree_9(features, features_length); votes[_class] += 1;
    
        int32_t most_voted_class = -1;
        int32_t most_voted_votes = 0;
        for (int32_t i=0; i<2; i++) {

            if (votes[i] > most_voted_votes) {
                most_voted_class = i;
                most_voted_votes = votes[i];
            }
        }
        return most_voted_class;
    }
    

int har_model_predict_proba(const float *features, int32_t features_length, float *out, int out_length) {

        int32_t _class = -1;

        for (int i=0; i<out_length; i++) {
            out[i] = 0.0f;
        }

        _class = har_model_tree_0(features, features_length); out[_class] += 1.0f;
    _class = har_model_tree_1(features, features_length); out[_class] += 1.0f;
    _class = har_model_tree_2(features, features_length); out[_class] += 1.0f;
    _class = har_model_tree_3(features, features_length); out[_class] += 1.0f;
    _class = har_model_tree_4(features, features_length); out[_class] += 1.0f;
    _class = har_model_tree_5(features, features_length); out[_class] += 1.0f;
    _class = har_model_tree_6(features, features_length); out[_class] += 1.0f;
    _class = har_model_tree_7(features, features_length); out[_class] += 1.0f;
    _class = har_model_tree_8(features, features_length); out[_class] += 1.0f;
    _class = har_model_tree_9(features, features_length); out[_class] += 1.0f;
    
        // compute mean
        for (int i=0; i<out_length; i++) {
            out[i] = out[i] / 10;
        }
        return 0;
    }
    